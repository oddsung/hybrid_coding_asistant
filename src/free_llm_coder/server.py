"""OpenAI-compatible HTTP API server backed by the web LLM services.

Exposes ``/v1/models`` and ``/v1/chat/completions`` (with SSE streaming) so
existing chat front-ends -- Open WebUI, LM Studio, any OpenAI SDK -- can use
the browser-driven free services as if they were API models. Each configured
service is listed as a model; the ``auto`` model picks by priority with
limit-based rotation, exactly like the CLI chat loop.

Three constraints shape the design:

- Playwright's sync API must stay on the thread that started it, so ALL
  browser work happens on one dedicated worker thread. HTTP requests are
  queued and processed strictly one at a time.
- Web chat UIs keep their own conversation history server-side. We therefore
  send a service only what it is missing: normally just the newest user
  message, but when a service takes over mid-conversation (rotation after a
  usage limit, or a server restart) it first gets a handoff transcript of the
  turns it has not seen -- reconstructed from the front-end's message list.
- The worker tracks one conversation at a time (per-service "seen" counts
  plus a fingerprint of the first user message). Interleaving two different
  front-end conversations resets that tracking, which costs a redundant
  handoff, not correctness.
"""
import hashlib
import json
import queue
import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Optional

from fastapi import FastAPI, HTTPException
from fastapi.responses import StreamingResponse

from .conversation import HANDOFF_HEADER, render_transcript
from .manager.service_manager import ServiceManager
from .logging_setup import get_logger

log = get_logger("server")

# How long a non-streaming request waits for the browser exchange to finish.
REQUEST_TIMEOUT = 600
# How long the SSE stream tolerates silence (no partial updates) before
# giving up. Generous because a queued request waits for earlier ones.
STREAM_IDLE_TIMEOUT = 600


# --------------------------------------------------------------------------- #
# OpenAI message-list -> single web-chat prompt
# --------------------------------------------------------------------------- #
def _content_text(content) -> str:
    """Flatten an OpenAI ``content`` field (string or parts list) to text."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(
            part.get("text", "")
            for part in content
            if isinstance(part, dict) and part.get("type") == "text"
        )
    return ""


def extract_prompt(messages: list) -> tuple[str, bool]:
    """Return ``(prompt, is_new_conversation)`` for an OpenAI messages list.

    The web chat holds the history, so only the newest user message is sent.
    A request with no assistant messages is a brand-new conversation; system
    messages are prepended to its first prompt (there is no other way to give
    a web chat a system prompt).
    """
    last_user = next((m for m in reversed(messages) if m.get("role") == "user"), None)
    prompt = _content_text(last_user.get("content")) if last_user else ""

    new_conversation = not any(m.get("role") == "assistant" for m in messages)
    if new_conversation and prompt:
        system_text = "\n".join(
            t for t in (_content_text(m.get("content"))
                        for m in messages if m.get("role") == "system")
            if t
        )
        if system_text:
            prompt = f"[System Instructions]\n{system_text}\n\n{prompt}"
    return prompt, new_conversation


def conversation_fingerprint(messages: list) -> str:
    """Identity of a front-end conversation: hash of its first user message."""
    first_user = next((m for m in messages if m.get("role") == "user"), None)
    text = _content_text(first_user.get("content")) if first_user else ""
    return hashlib.sha1(text.encode("utf-8", "ignore")).hexdigest()


def build_service_prompt(messages: list, seen_count: int) -> str:
    """Prompt for a service that already knows the first ``seen_count``
    messages of this conversation.

    A service that is up to date gets only the newest user message. A service
    entering mid-conversation additionally gets a handoff transcript of the
    unseen turns, and -- if it has seen nothing -- the system messages.
    """
    last_user_idx = next(
        (i for i in range(len(messages) - 1, -1, -1) if messages[i].get("role") == "user"),
        None,
    )
    if last_user_idx is None:
        return ""
    prompt = _content_text(messages[last_user_idx].get("content"))
    if not prompt:
        return ""

    unseen = [
        (m.get("role"), _content_text(m.get("content")))
        for i, m in enumerate(messages[seen_count:], start=seen_count)
        if i != last_user_idx and m.get("role") in ("user", "assistant")
    ]
    unseen = [(role, text) for role, text in unseen if text]

    parts = []
    if seen_count == 0:
        system_text = "\n".join(
            t for t in (_content_text(m.get("content"))
                        for m in messages if m.get("role") == "system")
            if t
        )
        if system_text:
            parts.append(f"[System Instructions]\n{system_text}")
    if unseen:
        parts.append(
            f"{HANDOFF_HEADER}\n\n--- Transcript ---\n"
            f"{render_transcript(unseen)}\n--- End of Transcript ---"
        )
    parts.append(prompt)
    return "\n\n".join(parts)


# --------------------------------------------------------------------------- #
# Single-threaded browser worker
# --------------------------------------------------------------------------- #
@dataclass
class ChatJob:
    messages: list                  # the request's OpenAI message list
    service: Optional[str]          # None = auto (priority order)
    new_chat: bool                  # request contains no assistant messages
    updates: "queue.Queue[Optional[str]]" = field(default_factory=queue.Queue)
    done: threading.Event = field(default_factory=threading.Event)
    result: Optional[tuple] = None  # (service_name, response_text)
    error: Optional[str] = None


class ChatWorker:
    """Owns the ServiceManager (and thus Playwright) on one thread.

    Jobs are processed in submission order; the rotation/limit/empty-response
    logic mirrors the CLI chat loop in main.py. Across jobs the worker tracks
    which conversation is active and how many of its messages each service
    has seen, so a rotation target gets a handoff instead of an amnesiac
    single question.
    """

    def __init__(self, config: dict):
        self.config = config
        self.jobs: "queue.Queue[Optional[ChatJob]]" = queue.Queue()
        # Conversation tracking (worker thread only).
        self._conv_fp: Optional[str] = None
        self._conv_len = 0
        self._seen: dict = {}       # service name -> messages known to it
        self.thread = threading.Thread(target=self._run, name="chat-worker", daemon=True)
        self.thread.start()

    def submit(self, job: ChatJob):
        self.jobs.put(job)

    def _run(self):
        svc_mgr = ServiceManager(self.config)
        while True:
            job = self.jobs.get()
            if job is None:
                svc_mgr.close_all()
                return
            try:
                self._process(svc_mgr, job)
            except Exception as e:
                log.exception("job failed")
                job.error = job.error or str(e)
            finally:
                job.done.set()
                job.updates.put(None)  # end-of-stream sentinel

    def _sync_conversation(self, job: ChatJob):
        """Reset per-service tracking when the active conversation changed.

        A brand-new conversation (no assistant messages), a different first
        user message, or a message list that shrank (regenerate / switched
        threads) all mean the old "seen" counts no longer apply. The cost of
        a wrong reset is only a redundant handoff.
        """
        fp = conversation_fingerprint(job.messages)
        if job.new_chat or fp != self._conv_fp or len(job.messages) < self._conv_len:
            self._seen = {}
            self._conv_fp = fp
        self._conv_len = len(job.messages)

    def _process(self, svc_mgr: ServiceManager, job: ChatJob):
        self._sync_conversation(job)

        svc_mgr.reset_rotation()
        if job.service:
            if not svc_mgr.select_service(job.service):
                job.error = f"service '{job.service}' is unknown or on cooldown"
                return
        if svc_mgr.is_exhausted():
            job.error = "all services are on cooldown; try again shortly"
            return

        empty_retries = 0
        attempts = 0
        max_total_attempts = max(2, len(svc_mgr.services_config) * 2)

        while attempts < max_total_attempts:
            attempts += 1
            service_name = "unknown"
            try:
                driver, service_name = svc_mgr.get_active()
                log.info("processing prompt via '%s' (attempt %d)", service_name, attempts)

                # A service that has seen nothing of this conversation starts
                # a fresh web chat and receives a handoff transcript.
                seen = self._seen.get(service_name, 0)
                prompt = build_service_prompt(job.messages, seen)
                if seen == 0:
                    driver.new_chat()

                driver.send_message(prompt)
                response = driver.wait_for_response(on_update=job.updates.put)

                if driver.is_limit_reached():
                    log.info("limit reached on '%s'; rotating", service_name)
                    svc_mgr.rotate_service()
                    empty_retries = 0
                    if svc_mgr.is_exhausted():
                        break
                    continue

                if not response:
                    empty_retries += 1
                    if empty_retries >= 2:
                        svc_mgr.rotate_service()
                        empty_retries = 0
                        if svc_mgr.is_exhausted():
                            break
                    else:
                        time.sleep(2)
                    continue

                svc_mgr.mark_success()
                # This service now knows every request message plus the
                # answer it just gave (which the front-end will echo back
                # as the next request's assistant message).
                self._seen[service_name] = len(job.messages) + 1
                job.result = (service_name, response)
                return

            except Exception as e:
                log.warning("error with '%s': %s", service_name, e)
                svc_mgr.rotate_service()
                if svc_mgr.is_exhausted():
                    break

        job.error = "no service could answer this prompt (limits or errors on all)"


# --------------------------------------------------------------------------- #
# OpenAI response payloads
# --------------------------------------------------------------------------- #
def _completion_id() -> str:
    return f"chatcmpl-{uuid.uuid4().hex}"


def completion_json(text: str, model: str) -> dict:
    return {
        "id": _completion_id(),
        "object": "chat.completion",
        "created": int(time.time()),
        "model": model,
        "choices": [{
            "index": 0,
            "message": {"role": "assistant", "content": text},
            "finish_reason": "stop",
        }],
        # Web UIs expose no token counts; zeros keep OpenAI clients happy.
        "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
    }


def stream_delta(sent: str, partial: str) -> Optional[str]:
    """New suffix to emit given what was already streamed.

    Partial texts occasionally jitter (DOM extraction mid-render); anything
    that is not a pure extension of what was sent is skipped rather than
    re-sent, and the final full text is reconciled at the end of the stream.
    """
    if partial.startswith(sent) and len(partial) > len(sent):
        return partial[len(sent):]
    return None


def _sse(data) -> str:
    if isinstance(data, str):
        return f"data: {data}\n\n"
    return f"data: {json.dumps(data, ensure_ascii=False)}\n\n"


def sse_stream(job: ChatJob, model: str):
    """Yield OpenAI ``chat.completion.chunk`` SSE events for a job."""
    chunk_id = _completion_id()
    created = int(time.time())

    def chunk(delta: dict, finish: Optional[str] = None) -> dict:
        return {
            "id": chunk_id,
            "object": "chat.completion.chunk",
            "created": created,
            "model": model,
            "choices": [{"index": 0, "delta": delta, "finish_reason": finish}],
        }

    yield _sse(chunk({"role": "assistant", "content": ""}))

    sent = ""
    while True:
        try:
            partial = job.updates.get(timeout=STREAM_IDLE_TIMEOUT)
        except queue.Empty:
            job.error = job.error or "stream timed out waiting for the service"
            break
        if partial is None:  # worker finished (success or error)
            break
        delta = stream_delta(sent, partial)
        if delta:
            sent = partial
            yield _sse(chunk({"content": delta}))

    if job.result:
        _, text = job.result
        # Reconcile: emit whatever the partial updates missed.
        final_delta = stream_delta(sent, text)
        if final_delta:
            yield _sse(chunk({"content": final_delta}))
        elif not text.startswith(sent):
            # Extraction jitter changed earlier text; resend authoritative copy.
            yield _sse(chunk({"content": f"\n\n[final]\n{text}"}))
        yield _sse(chunk({}, finish="stop"))
    else:
        yield _sse(chunk({"content": f"\n[error] {job.error or 'unknown error'}"}, finish="stop"))
    yield _sse("[DONE]")


# --------------------------------------------------------------------------- #
# FastAPI app
# --------------------------------------------------------------------------- #
def create_app(config: dict) -> FastAPI:
    app = FastAPI(title="free-llm-coder", version="0.2.0")
    worker = ChatWorker(config)

    service_names = [s["name"] for s in config.get("services", [])]

    @app.get("/v1/models")
    def list_models():
        names = ["auto"] + service_names
        return {
            "object": "list",
            "data": [
                {"id": n, "object": "model", "created": 0, "owned_by": "free-llm-coder"}
                for n in names
            ],
        }

    # Endpoints are sync `def` on purpose: Starlette runs them (and the SSE
    # generator) on its threadpool, which is how the blocking queue handoff
    # with the single browser thread stays simple.
    @app.post("/v1/chat/completions")
    def chat_completions(payload: dict):
        messages = payload.get("messages") or []
        if not isinstance(messages, list) or not messages:
            raise HTTPException(status_code=400, detail="'messages' must be a non-empty list")

        model = payload.get("model") or "auto"
        service = None if model == "auto" else model
        if service and service not in service_names:
            raise HTTPException(status_code=404, detail=f"unknown model '{model}'")

        prompt, new_chat = extract_prompt(messages)
        if not prompt.strip():
            raise HTTPException(status_code=400, detail="no user message found")

        job = ChatJob(messages=messages, service=service, new_chat=new_chat)
        worker.submit(job)

        if payload.get("stream"):
            return StreamingResponse(sse_stream(job, model), media_type="text/event-stream")

        if not job.done.wait(timeout=REQUEST_TIMEOUT):
            raise HTTPException(status_code=504, detail="timed out waiting for the service")
        if job.error:
            raise HTTPException(status_code=502, detail=job.error)
        service_name, text = job.result
        return completion_json(text, service_name)

    return app
