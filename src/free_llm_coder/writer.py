"""Parse and safely apply structured ``file:<path>`` blocks from an LLM reply.

The LLM is instructed (see context.py output rules) to emit each file as a
fenced block whose info string is ``file:<relative/path>``::

    ```file:src/app.py
    <full file content>
    ```

This is parsed here and written with Python file I/O -- no shell, no heredoc --
so special characters and ``EOF`` collisions cannot break anything.
"""
import re
import difflib
from pathlib import Path
from typing import List, NamedTuple


class FileBlock(NamedTuple):
    path: str       # path exactly as written by the LLM
    content: str    # full intended file content


# Matches ```file:some/path\n ... ``` (info string is `file:<path>`).
_FILE_BLOCK_RE = re.compile(
    r"```file:[ \t]*(?P<path>[^\n`]+?)[ \t]*\n(?P<body>.*?)```",
    re.DOTALL,
)


def parse_file_blocks(text: str) -> List[FileBlock]:
    """Extract all ``file:<path>`` blocks from an LLM response."""
    blocks: List[FileBlock] = []
    for m in _FILE_BLOCK_RE.finditer(text):
        path = m.group("path").strip()
        body = m.group("body")
        # Drop exactly one trailing newline introduced by the closing fence.
        if body.endswith("\n"):
            body = body[:-1]
        if path:
            blocks.append(FileBlock(path=path, content=body))
    return blocks


def resolve_safe_path(base_dir, rel_path: str) -> Path:
    """Resolve ``rel_path`` under ``base_dir``, rejecting any escape.

    Raises ValueError if the path would land outside the project directory
    (absolute paths, ``..`` traversal, symlink escapes).
    """
    base = Path(base_dir).resolve()
    candidate = (base / rel_path).resolve()
    if candidate != base and base not in candidate.parents:
        raise ValueError(f"refusing to write outside the project directory: {rel_path}")
    if candidate == base:
        raise ValueError(f"invalid file path: {rel_path}")
    return candidate


def diff_preview(target: Path, new_content: str) -> str:
    """Return a unified diff from the file's current content to ``new_content``.

    For a new file this is effectively the whole content as additions.
    """
    old = ""
    if target.exists():
        try:
            old = target.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            old = ""
    diff = difflib.unified_diff(
        old.splitlines(keepends=True),
        new_content.splitlines(keepends=True),
        fromfile="current",
        tofile="proposed",
    )
    return "".join(diff)


def write_file(target: Path, content: str) -> None:
    """Write ``content`` to ``target``, creating parent directories as needed."""
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")
