"""Tests for the structured-file-write parser and path safety."""
import pytest

from free_llm_coder.writer import (
    parse_file_blocks,
    resolve_safe_path,
    diff_preview,
    write_file,
)


SAMPLE = """\
Here is the plan:
```file:src/app.py
print("hi")
x = 1
```

And another:
```file: subdir/util.py
def f():
    return 42
```

```bash
pip install x
```
"""


def test_parse_extracts_paths_and_content():
    blocks = parse_file_blocks(SAMPLE)
    assert [b.path for b in blocks] == ["src/app.py", "subdir/util.py"]
    assert blocks[0].content == 'print("hi")\nx = 1'
    assert blocks[1].content == "def f():\n    return 42"


def test_parse_returns_empty_on_no_blocks():
    assert parse_file_blocks("just prose, no fences") == []


def test_resolve_safe_path_accepts_inside(tmp_path):
    p = resolve_safe_path(tmp_path, "src/new.py")
    assert p == (tmp_path / "src" / "new.py").resolve()


@pytest.mark.parametrize("bad", ["../escape.py", "/etc/passwd", "../../x", "a/../../../x"])
def test_resolve_safe_path_rejects_traversal(tmp_path, bad):
    with pytest.raises(ValueError):
        resolve_safe_path(tmp_path, bad)


def test_resolve_safe_path_rejects_writing_to_base_itself(tmp_path):
    with pytest.raises(ValueError):
        resolve_safe_path(tmp_path, ".")


def test_diff_and_write_round_trip(tmp_path):
    target = tmp_path / "f.txt"
    target.write_text("old line\n")
    diff = diff_preview(target, "new line\n")
    assert "old line" in diff and "new line" in diff

    write_file(target, "new line\n")
    assert target.read_text() == "new line\n"


def test_write_creates_parent_dirs(tmp_path):
    target = tmp_path / "a" / "b" / "c.py"
    write_file(target, "content")
    assert target.read_text() == "content"
