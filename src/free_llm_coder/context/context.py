import os
import platform
import fnmatch
from pathlib import Path
from typing import List, Optional, Tuple

# File extensions treated as "source" and therefore prioritized when the
# context budget is tight.
SOURCE_EXTENSIONS = {
    ".py", ".js", ".ts", ".jsx", ".tsx", ".java", ".go", ".rs", ".rb",
    ".c", ".cpp", ".cc", ".h", ".hpp", ".cs", ".kt", ".swift", ".php",
    ".sh", ".bash", ".sql", ".html", ".css", ".scss",
    ".toml", ".yaml", ".yml", ".json", ".ini", ".cfg", ".md", ".txt",
}

# Output rules sent once at the start of a chat session.
_OUTPUT_RULES = (
    "*** STRICT OUTPUT RULES ***\n"
    "1. **NO CONVERSATIONAL TEXT**: Output only fenced code blocks; no prose outside them.\n"
    "2. **CREATING OR MODIFYING FILES**:\n"
    "   - For each file, output a fenced block whose info string is `file:<relative/path>`:\n"
    "       ```file:src/app.py\n"
    "       <full file content here>\n"
    "       ```\n"
    "   - Always output the COMPLETE file content -- never a diff, patch, or fragment.\n"
    "   - Paths are relative to the project root. NEVER use absolute paths or `..`.\n"
    "   - Do NOT use `cat << 'EOF'` or bash blocks to create files.\n"
    "3. **SHELL COMMANDS**:\n"
    "   - Use ` ```bash ` blocks ONLY for commands to RUN (e.g. `pip install`, tests).\n"
    "   - **DO NOT** output commands to run the app unless the user explicitly asks 'Run this'.\n"
    "4. **DOCUMENTATION**: Put all explanations in `README.md` or code comments -- never as chat text.\n"
    "5. **MISSING CONTEXT**: If a file you need was omitted from the context below,\n"
    "   ask for it explicitly (e.g. 'Show me path/to/file') instead of guessing.\n"
)


class ContextManager:
    def __init__(self, root_path: str = ".", config: Optional[dict] = None):
        self.root_path = Path(root_path).resolve()
        self.ignore_patterns = self._load_gitignore()
        # Default ignore patterns
        self.ignore_patterns.extend([
            ".git", "__pycache__", ".DS_Store", "venv", "node_modules",
            "*.pyc", "user_data", ".gemini"
        ])

        ctx_cfg = (config or {}).get("context", {}) or {}
        self.max_files = ctx_cfg.get("max_files", 20)
        self.max_chars = ctx_cfg.get("max_chars", 40000)

        # Maps an absolute file path to the mtime it had when it was last
        # included in a prompt. Used to send only changed files on later turns.
        self.sent_files: dict = {}

    def reset(self):
        """Forget what has been sent so the next prompt rebuilds full context.

        Call this when the chat session is reset (e.g. the ``/new`` command).
        """
        self.sent_files = {}

    def _load_gitignore(self) -> List[str]:
        """Load patterns from .gitignore. Trailing slashes (directory form,
        e.g. ``build/``) are stripped so fnmatch can match against path parts.
        """
        gitignore_path = self.root_path / ".gitignore"
        patterns = []
        if gitignore_path.exists():
            with open(gitignore_path, "r") as f:
                for line in f:
                    line = line.strip().rstrip("/")
                    if line and not line.startswith("#"):
                        patterns.append(line)
        return patterns

    def _is_ignored(self, file_path: Path) -> bool:
        try:
            rel_path = file_path.relative_to(self.root_path)
        except ValueError:
            return True # Path is not relative to root

        for pattern in self.ignore_patterns:
            if fnmatch.fnmatch(str(rel_path), pattern) or \
               fnmatch.fnmatch(file_path.name, pattern):
                return True
            # Check directory partials
            for part in rel_path.parts:
                if fnmatch.fnmatch(part, pattern):
                    return True
        return False

    def scan_files(self, max_depth: int = 3) -> List[Path]:
        found_files = []
        for root, dirs, files in os.walk(self.root_path):
            # Modify dirs in-place to skip ignored directories
            dirs[:] = [d for d in dirs if not self._is_ignored(Path(root) / d)]

            # Check depth: scan files at this level, but stop descending once
            # max_depth is reached so files exactly at max_depth are not lost.
            current_depth = len(Path(root).relative_to(self.root_path).parts)
            if current_depth >= max_depth:
                dirs[:] = [] # Do not recurse deeper

            for file in files:
                file_path = Path(root) / file
                if not self._is_ignored(file_path):
                    found_files.append(file_path)

        return sorted(found_files)

    def _priority_key(self, path: Path) -> tuple:
        """Sort key: source files first, then smaller files first."""
        is_source = 0 if path.suffix.lower() in SOURCE_EXTENSIONS else 1
        try:
            size = path.stat().st_size
        except OSError:
            size = 1 << 30
        return (is_source, size)

    def _build_context_section(self, files: List[Path], limit_count: bool) -> Tuple[str, List[Path]]:
        """Render file contents within the max_files / max_chars budget.

        Returns ``(context_string, included_paths)``. Files that do not fit the
        budget appear as a header-only placeholder so the LLM knows they exist.
        """
        ordered = sorted(files, key=self._priority_key)
        if limit_count:
            ordered = ordered[: self.max_files]

        parts = [f"Project Root: {self.root_path}"]
        included: List[Path] = []
        char_budget = self.max_chars

        for fp in ordered:
            try:
                rel = fp.relative_to(self.root_path)
            except ValueError:
                continue
            try:
                size = fp.stat().st_size
            except OSError:
                continue

            if size > 50 * 1024:
                parts.append(f"\n[FILE: {rel}] (skipped: larger than 50KB)")
                continue

            try:
                content = fp.read_text(encoding="utf-8", errors="ignore")
            except Exception as e:
                parts.append(f"\n[FILE: {rel}] (error reading: {e})")
                continue

            if len(content) > char_budget:
                parts.append(
                    f"\n[FILE: {rel}] (omitted: {len(content)} chars over the context "
                    f"budget -- ask to see this file if you need it)"
                )
                continue

            parts.append(f"\n[FILE: {rel}]\n{content}")
            char_budget -= len(content)
            included.append(fp)

        return "\n".join(parts), included

    def _mark_sent(self, files: List[Path]):
        for fp in files:
            try:
                self.sent_files[fp] = fp.stat().st_mtime
            except OSError:
                pass

    def _changed_files(self, files: List[Path]) -> List[Path]:
        """Files that are new or whose mtime advanced since they were last sent."""
        changed = []
        for fp in files:
            try:
                mtime = fp.stat().st_mtime
            except OSError:
                continue
            prev = self.sent_files.get(fp)
            if prev is None or mtime > prev:
                changed.append(fp)
        return changed

    def _system_info(self) -> str:
        return f"OS: {platform.system()} {platform.release()}\nCurrent Directory: {self.root_path}"

    def build_initial_prompt(self, user_query: str) -> str:
        """First turn: full output rules + budgeted project context."""
        files = self.scan_files()
        context_str, included = self._build_context_section(files, limit_count=True)
        self._mark_sent(included)

        return (
            "You are an automated AI coding assistant. \n"
            f"{self._system_info()}\n\n"
            f"{_OUTPUT_RULES}\n"
            "--- Project Files ---\n"
            f"{context_str}\n\n"
            "--- End of Context ---\n\n"
            f"User Question: {user_query}"
        )

    def build_followup_prompt(self, user_query: str) -> str:
        """Later turns: only changed/new files (the web chat keeps history)."""
        files = self.scan_files()
        changed = self._changed_files(files)

        if changed:
            context_str, included = self._build_context_section(changed, limit_count=False)
            self._mark_sent(included)
            context_block = (
                "--- Updated / New Project Files ---\n"
                f"{context_str}\n\n"
                "--- End of Context ---\n\n"
            )
        else:
            context_block = (
                "(No project files changed since the last message. "
                "Use the project context already provided earlier.)\n\n"
            )

        return f"{context_block}User Question: {user_query}"

    def build_prompt(self, user_query: str) -> str:
        """Build a prompt, full on the first turn and incremental afterwards."""
        if not self.sent_files:
            return self.build_initial_prompt(user_query)
        return self.build_followup_prompt(user_query)
