"""Cross-service conversation continuity.

Each web chat service keeps its own history, so a conversation that starts on
one service exists only there. When a usage limit forces rotation to another
service, the new service knows nothing about the dialogue so far. This module
keeps the program's own transcript and tracks how much of it each service has
seen, so that switching services prepends a handoff block containing exactly
the turns the target service is missing -- nothing more.

Used by both the CLI chat loop (main.py) and the API server (server.py).
"""
from typing import Dict, List, Tuple

# Total budget for a handoff transcript. Oldest turns are dropped first;
# what matters most when taking over a conversation is the recent exchange.
DEFAULT_MAX_HANDOFF_CHARS = 12000
# A single turn (usually a long assistant answer) may not eat the whole budget.
PER_TURN_MAX_CHARS = 2500

HANDOFF_HEADER = (
    "[Conversation Handoff]\n"
    "You are taking over an ongoing conversation; the earlier turns below may "
    "have happened with another assistant. Read the transcript, then answer "
    "the user's newest message at the end naturally. Do not mention this "
    "handoff, do not summarize the transcript, and do not re-introduce "
    "yourself -- just continue the conversation."
)


def _clip(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[:limit] + "\n... (rest of this turn truncated)"


def render_transcript(
    turns: List[Tuple[str, str]],
    max_chars: int = DEFAULT_MAX_HANDOFF_CHARS,
    per_turn_max: int = PER_TURN_MAX_CHARS,
) -> str:
    """Render ``(role, text)`` turns oldest-first within the char budget.

    Newest turns are kept preferentially; when the budget runs out, older
    turns are replaced by a single omission marker.
    """
    rendered: List[str] = []
    total = 0
    for role, text in reversed(turns):
        label = "User" if role == "user" else "Assistant"
        entry = f"[{label}]: {_clip(text.strip(), per_turn_max)}"
        if rendered and total + len(entry) > max_chars:
            rendered.append("[... older turns omitted ...]")
            break
        rendered.append(entry)
        total += len(entry)
    return "\n\n".join(reversed(rendered))


class Conversation:
    """The program's own transcript plus per-service visibility tracking."""

    def __init__(self, max_handoff_chars: int = DEFAULT_MAX_HANDOFF_CHARS):
        self.turns: List[Tuple[str, str]] = []   # ("user" | "assistant", text)
        self.seen: Dict[str, int] = {}           # service -> turns known to it
        self.max_handoff_chars = max_handoff_chars

    def record(self, role: str, text: str):
        self.turns.append((role, text))

    def mark_seen(self, service: str):
        """Declare that ``service`` now knows the whole transcript (call after
        it successfully answered)."""
        self.seen[service] = len(self.turns)

    def unseen_turns(self, service: str) -> List[Tuple[str, str]]:
        return self.turns[self.seen.get(service, 0):]

    def handoff_block(self, service: str) -> str:
        """Handoff text for the turns ``service`` has not seen.

        Empty string when the service is up to date (the common case: the same
        service keeps answering and its own web chat holds the history).
        """
        unseen = self.unseen_turns(service)
        if not unseen:
            return ""
        transcript = render_transcript(unseen, self.max_handoff_chars)
        return (
            f"{HANDOFF_HEADER}\n\n"
            f"--- Transcript ---\n{transcript}\n--- End of Transcript ---\n\n"
        )

    def reset(self):
        self.turns = []
        self.seen = {}
