"""Tests for parsers exposed from main."""
from free_llm_coder.main import _extract_bash_blocks


def test_extract_bash_blocks_handles_bash_shell_sh():
    text = "intro\n```bash\necho hi\n```\nmid\n```shell\nls\n```\n```sh\npwd\n```\n"
    blocks = _extract_bash_blocks(text)
    assert blocks == ["echo hi", "ls", "pwd"]


def test_extract_bash_blocks_returns_empty_when_no_blocks():
    assert _extract_bash_blocks("no fences here") == []


def test_extract_bash_blocks_ignores_other_languages():
    text = "```python\nprint(1)\n```"
    assert _extract_bash_blocks(text) == []
