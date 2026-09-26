"""Tests for cross-service conversation continuity."""
from free_llm_coder.conversation import Conversation, render_transcript


def _seeded():
    c = Conversation()
    c.record("user", "q1")
    c.record("assistant", "a1")
    c.mark_seen("chatgpt")
    c.record("user", "q2")
    c.record("assistant", "a2")
    c.mark_seen("chatgpt")
    return c


def test_up_to_date_service_gets_no_handoff():
    c = _seeded()
    assert c.handoff_block("chatgpt") == ""


def test_new_service_gets_full_transcript():
    c = _seeded()
    block = c.handoff_block("gemini")
    for text in ("q1", "a1", "q2", "a2"):
        assert text in block
    assert "Conversation Handoff" in block


def test_returning_service_gets_only_missed_turns():
    c = _seeded()
    # gemini answers the next turn; chatgpt misses it.
    c.record("user", "q3")
    c.record("assistant", "a3")
    c.mark_seen("gemini")

    block = c.handoff_block("chatgpt")
    assert "q3" in block and "a3" in block
    assert "q1" not in block and "a1" not in block


def test_reset_clears_transcript_and_seen():
    c = _seeded()
    c.reset()
    assert c.turns == []
    assert c.handoff_block("gemini") == ""


def test_render_transcript_drops_oldest_when_over_budget():
    turns = [("user", f"question {i} " + "x" * 200) for i in range(20)]
    out = render_transcript(turns, max_chars=1000)
    assert "question 19" in out           # newest kept
    assert "question 0" not in out        # oldest dropped
    assert "older turns omitted" in out


def test_render_transcript_clips_giant_single_turn():
    turns = [("assistant", "y" * 10000)]
    out = render_transcript(turns, max_chars=5000, per_turn_max=100)
    assert "truncated" in out
    assert len(out) < 1000
