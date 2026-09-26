"""Tests for parsers and prompt-label helpers exposed from main."""
from free_llm_coder.config_schema import build_default_config
from free_llm_coder.main import _extract_bash_blocks, _prompt_label
from free_llm_coder.manager.service_manager import ServiceManager, FAILURE_THRESHOLD


def _mgr():
    cfg = build_default_config()
    cfg["browser"]["user_data_dir"] = "/tmp/fct_ud"
    return ServiceManager(cfg)


def test_prompt_label_shows_next_service():
    sm = _mgr()
    assert _prompt_label(sm, None) == "chatgpt"
    assert _prompt_label(sm, "qwen") == "qwen"          # /switch preference
    assert _prompt_label(sm, "no-such") == "chatgpt"    # unknown -> priority order


def test_prompt_label_skips_cooled_down_service():
    sm = _mgr()
    for _ in range(FAILURE_THRESHOLD):
        sm.record_failure("chatgpt")
    assert _prompt_label(sm, None) == "gemini"
    assert _prompt_label(sm, "chatgpt") == "gemini"     # preferred on cooldown


def test_prompt_label_includes_known_model():
    sm = _mgr()

    class FakeDriver:
        current_model = "Qwen3.8-Max"

    sm.drivers["qwen"] = FakeDriver()
    assert _prompt_label(sm, "qwen") == "qwen · Qwen3.8-Max"


def test_extract_bash_blocks_handles_bash_shell_sh():
    text = "intro\n```bash\necho hi\n```\nmid\n```shell\nls\n```\n```sh\npwd\n```\n"
    blocks = _extract_bash_blocks(text)
    assert blocks == ["echo hi", "ls", "pwd"]


def test_extract_bash_blocks_returns_empty_when_no_blocks():
    assert _extract_bash_blocks("no fences here") == []


def test_extract_bash_blocks_ignores_other_languages():
    text = "```python\nprint(1)\n```"
    assert _extract_bash_blocks(text) == []
