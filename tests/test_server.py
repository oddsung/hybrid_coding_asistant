"""Tests for the OpenAI-compatible server's pure helpers.

The FastAPI app itself needs a browser worker, so these tests target the
message-to-prompt conversion, payload shapes, and stream-delta logic that the
endpoints are built from -- all browser-free.
"""
import pytest

from free_llm_coder.server import (
    extract_prompt,
    completion_json,
    stream_delta,
    build_service_prompt,
    conversation_fingerprint,
    parse_model_id,
)


# ------------------------------ extract_prompt ----------------------------- #
def test_first_request_is_new_conversation_with_system_prepended():
    messages = [
        {"role": "system", "content": "You are terse."},
        {"role": "user", "content": "hello"},
    ]
    prompt, new_chat = extract_prompt(messages)
    assert new_chat is True
    assert "You are terse." in prompt
    assert prompt.endswith("hello")


def test_followup_sends_only_last_user_message():
    messages = [
        {"role": "system", "content": "You are terse."},
        {"role": "user", "content": "hello"},
        {"role": "assistant", "content": "hi"},
        {"role": "user", "content": "second question"},
    ]
    prompt, new_chat = extract_prompt(messages)
    assert new_chat is False
    assert prompt == "second question"
    assert "You are terse." not in prompt


def test_content_parts_list_is_flattened():
    messages = [
        {"role": "user", "content": [
            {"type": "text", "text": "line one"},
            {"type": "image_url", "image_url": {"url": "ignored"}},
            {"type": "text", "text": "line two"},
        ]},
    ]
    prompt, _ = extract_prompt(messages)
    assert prompt == "line one\nline two"


def test_no_user_message_yields_empty_prompt():
    prompt, _ = extract_prompt([{"role": "system", "content": "x"}])
    assert prompt == ""


# --------------------------- per-service handoff --------------------------- #
_CONVO = [
    {"role": "system", "content": "Be brief."},
    {"role": "user", "content": "q1"},
    {"role": "assistant", "content": "a1"},
    {"role": "user", "content": "q2"},
]


def test_up_to_date_service_gets_only_last_message():
    # Service answered q1 (saw messages 0..2, then its own a1 => seen=3... the
    # front-end list here has 4 entries; the service saw the first 3).
    prompt = build_service_prompt(_CONVO, seen_count=3)
    assert prompt == "q2"


def test_fresh_service_gets_system_plus_full_handoff():
    prompt = build_service_prompt(_CONVO, seen_count=0)
    assert "[System Instructions]" in prompt and "Be brief." in prompt
    assert "Conversation Handoff" in prompt
    assert "q1" in prompt and "a1" in prompt
    assert prompt.rstrip().endswith("q2")


def test_partially_caught_up_service_gets_only_missed_turns():
    convo = _CONVO + [
        {"role": "assistant", "content": "a2"},
        {"role": "user", "content": "q3"},
    ]
    # Service saw through a2 (first 5 messages): only q3 remains.
    assert build_service_prompt(convo, seen_count=5) == "q3"
    # Service that left after a1 (saw 3): missed q2/a2, handoff has just those.
    prompt = build_service_prompt(convo, seen_count=3)
    assert "q2" in prompt and "a2" in prompt
    assert "q1" not in prompt and "[System Instructions]" not in prompt
    assert prompt.rstrip().endswith("q3")


def test_fingerprint_tracks_first_user_message():
    a = conversation_fingerprint([{"role": "user", "content": "hello"}])
    b = conversation_fingerprint([
        {"role": "user", "content": "hello"},
        {"role": "assistant", "content": "hi"},
        {"role": "user", "content": "more"},
    ])
    c = conversation_fingerprint([{"role": "user", "content": "different"}])
    assert a == b       # same conversation as it grows
    assert a != c       # different conversation


# ------------------------------ payload shape ------------------------------ #
def test_completion_json_matches_openai_shape():
    payload = completion_json("answer", "chatgpt")
    assert payload["object"] == "chat.completion"
    assert payload["model"] == "chatgpt"
    choice = payload["choices"][0]
    assert choice["message"] == {"role": "assistant", "content": "answer"}
    assert choice["finish_reason"] == "stop"
    assert "usage" in payload


# ------------------------------ model id parsing --------------------------- #
_NAMES = ["chatgpt", "qwen"]


def test_parse_model_id_forms():
    assert parse_model_id("auto", _NAMES) == (None, None)
    assert parse_model_id(None, _NAMES) == (None, None)
    assert parse_model_id("qwen", _NAMES) == ("qwen", None)
    assert parse_model_id("qwen/Qwen3.8-Max", _NAMES) == ("qwen", "Qwen3.8-Max")


def test_parse_model_id_rejects_unknown_service():
    with pytest.raises(ValueError):
        parse_model_id("nope/whatever", _NAMES)
    with pytest.raises(ValueError):
        parse_model_id("nope", _NAMES)


# ------------------------------ stream deltas ------------------------------ #
def test_stream_delta_emits_only_new_suffix():
    assert stream_delta("", "hel") == "hel"
    assert stream_delta("hel", "hello") == "lo"


def test_stream_delta_skips_jitter():
    # Shrunk or rewritten partials are skipped (reconciled at end of stream).
    assert stream_delta("hello", "hel") is None
    assert stream_delta("hello", "goodbye") is None
    assert stream_delta("hello", "hello") is None
