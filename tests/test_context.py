"""Tests for project scanning and incremental prompt building."""
import time

from free_llm_coder.context.context import ContextManager


def _seed_project(root):
    (root / "a.py").write_text("print('a')\n")
    (root / "b.py").write_text("print('b')\n")
    sub = root / "sub"
    sub.mkdir()
    (sub / "c.py").write_text("print('c')\n")
    (root / ".gitignore").write_text("ignore_me/\n")
    (root / "ignore_me").mkdir()
    (root / "ignore_me" / "x.py").write_text("nope")


def test_scan_respects_gitignore_and_default_ignores(tmp_path):
    _seed_project(tmp_path)
    (tmp_path / "__pycache__").mkdir()
    (tmp_path / "__pycache__" / "junk.pyc").write_text("nope")

    cm = ContextManager(str(tmp_path))
    rel = sorted(str(p.relative_to(tmp_path)) for p in cm.scan_files())
    assert "a.py" in rel and "sub/c.py" in rel
    assert "ignore_me/x.py" not in rel
    assert all("__pycache__" not in r for r in rel)


def test_scan_includes_max_depth_files(tmp_path):
    (tmp_path / "a" / "b" / "c").mkdir(parents=True)
    (tmp_path / "a" / "b" / "c" / "leaf.py").write_text("x")
    cm = ContextManager(str(tmp_path))
    rel = [str(p.relative_to(tmp_path)) for p in cm.scan_files(max_depth=3)]
    assert "a/b/c/leaf.py" in rel


def test_initial_prompt_full_followup_incremental(tmp_path):
    _seed_project(tmp_path)
    cm = ContextManager(str(tmp_path), config={"context": {"max_files": 10, "max_chars": 10000}})

    p1 = cm.build_prompt("hello")
    cm.commit()
    assert "STRICT OUTPUT RULES" in p1
    assert "a.py" in p1 and "b.py" in p1 and "sub/c.py" in p1

    p2 = cm.build_prompt("still hello")
    cm.commit()
    assert "No project files changed" in p2
    assert "STRICT OUTPUT RULES" not in p2

    time.sleep(0.01)
    (tmp_path / "b.py").write_text("print('b2')\n")
    p3 = cm.build_prompt("after edit")
    assert "Updated / New" in p3
    assert "b.py" in p3
    assert "a.py" not in p3


def test_uncommitted_context_is_resent(tmp_path):
    """A failed attempt (no commit) must not count as delivered."""
    _seed_project(tmp_path)
    cm = ContextManager(str(tmp_path))
    cm.build_prompt("first try")          # no commit: the send failed
    p_retry = cm.build_prompt("retry")
    assert "STRICT OUTPUT RULES" in p_retry
    assert "a.py" in p_retry


def test_context_is_tracked_per_service(tmp_path):
    """A rotation target that never saw the project gets full context."""
    _seed_project(tmp_path)
    cm = ContextManager(str(tmp_path))

    cm.build_prompt("hello", service="chatgpt")
    cm.commit("chatgpt")

    # Same conversation moves to gemini: full context again.
    p_gemini = cm.build_prompt("hello again", service="gemini")
    assert "STRICT OUTPUT RULES" in p_gemini
    assert "a.py" in p_gemini

    # chatgpt stays incremental.
    p_chatgpt = cm.build_prompt("hello again", service="chatgpt")
    assert "No project files changed" in p_chatgpt


def test_chat_mode_sends_question_verbatim(tmp_path):
    _seed_project(tmp_path)
    cm = ContextManager(str(tmp_path), mode="chat")
    assert cm.build_prompt("김치찌개 맛있게 끓이는 법?") == "김치찌개 맛있게 끓이는 법?"
    # No file context, no rules, regardless of service or turn count.
    assert cm.build_prompt("두 번째 질문", service="gemini") == "두 번째 질문"


def test_reset_returns_to_initial(tmp_path):
    _seed_project(tmp_path)
    cm = ContextManager(str(tmp_path))
    cm.build_prompt("first")
    cm.commit()
    cm.reset()
    p = cm.build_prompt("again")
    assert "STRICT OUTPUT RULES" in p
    assert "a.py" in p


def test_max_chars_budget_omits_large_files(tmp_path):
    (tmp_path / "small.py").write_text("x" * 50)
    (tmp_path / "big.py").write_text("y" * 5000)
    cm = ContextManager(str(tmp_path), config={"context": {"max_files": 10, "max_chars": 200}})
    p = cm.build_prompt("show")
    assert "small.py" in p
    # big.py should appear as omitted header but not its full body
    assert "over the context budget" in p
    assert "y" * 1000 not in p
