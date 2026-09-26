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
    """Builds prompts for the web services.

    ``mode`` selects what a prompt contains:

    - ``"code"``: coding assistant. Structured output rules + budgeted project
      file context (full on a service's first prompt, changed files after).
    - ``"chat"``: general-purpose Q&A. The user's question is sent verbatim --
      no rules, no project files. Web LLMs are already general assistants, so
      no instructions are needed at all.

    What has been sent is tracked **per service**: when rotation switches to a
    service mid-session, that service still gets the full project context on
    its first prompt. Files are only recorded as sent once :meth:`commit` is
    called (after a successful exchange), so a failed attempt re-sends them.
    """

    def __init__(self, root_path: str = ".", config: Optional[dict] = None,
                 mode: str = "code"):
        if mode not in ("code", "chat"):
            raise ValueError(f"unknown mode '{mode}' (expected 'code' or 'chat')")
        self.mode = mode
        self.root_path = Path(root_path).resolve()
        self.ignore_patterns = self._load_gitignore()
        # Default ignore patterns
        self.ignore_patterns.extend([
            ".git", "__pycache__", ".DS_Store", "venv", "node_modules",
            "*.pyc", "user_data", ".gemini"
        ])

        ctx_cfg = (config or {}).get("context", {}) or {}
        # Fallbacks mirror config_schema.DEFAULT_CONTEXT.
        self.max_files = ctx_cfg.get("max_files", 25)
        self.max_chars = ctx_cfg.get("max_chars", 60000)

        # Per-service map of {absolute path: mtime when last sent}. Only
        # changed files are re-sent to a service on its later turns.
        self.sent_files: dict = {}
        # Files staged by build_prompt but not yet confirmed delivered.
        self._pending: dict = {}

    def reset(self):
        """Forget what has been sent so the next prompt rebuilds full context.

        Call this when the chat session is reset (e.g. the ``/new`` command).
        """
        self.sent_files = {}
        self._pending = {}

    def commit(self, service: str = "default"):
        """Record the staged files as actually delivered to ``service``.

        Call after a successful exchange; skipping it on failure means the
        next attempt re-sends the same files instead of assuming they arrived.
        """
        staged = self._pending.pop(service, None)
        if staged:
            self.sent_files.setdefault(service, {}).update(staged)

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

    def scan_files(self, max_depth: int = 5) -> List[Path]:
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

    def _stage_sent(self, service: str, files: List[Path]):
        staged = self._pending.setdefault(service, {})
        for fp in files:
            try:
                staged[fp] = fp.stat().st_mtime
            except OSError:
                pass

    def _changed_files(self, files: List[Path], service: str) -> List[Path]:
        """Files that are new or whose mtime advanced since they were last
        sent to ``service``."""
        sent = self.sent_files.get(service, {})
        changed = []
        for fp in files:
            try:
                mtime = fp.stat().st_mtime
            except OSError:
                continue
            prev = sent.get(fp)
            if prev is None or mtime > prev:
                changed.append(fp)
        return changed

    def _system_info(self) -> str:
        return f"OS: {platform.system()} {platform.release()}\nCurrent Directory: {self.root_path}"

    def build_initial_prompt(self, user_query: str, service: str = "default") -> str:
        """A service's first turn: full output rules + budgeted project context."""
        files = self.scan_files()
        context_str, included = self._build_context_section(files, limit_count=True)
        self._stage_sent(service, included)

        return (
            "You are an automated AI coding assistant. \n"
            f"{self._system_info()}\n\n"
            f"{_OUTPUT_RULES}\n"
            "--- Project Files ---\n"
            f"{context_str}\n\n"
            "--- End of Context ---\n\n"
            f"User Question: {user_query}"
        )

    def build_followup_prompt(self, user_query: str, service: str = "default") -> str:
        """Later turns: only files changed since this service last saw them."""
        files = self.scan_files()
        changed = self._changed_files(files, service)

        if changed:
            context_str, included = self._build_context_section(changed, limit_count=False)
            self._stage_sent(service, included)
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

    def build_prompt(self, user_query: str, service: str = "default") -> str:
        """Build the prompt to send to ``service`` for this turn.

        Chat mode sends the question verbatim. Code mode sends the full
        rules-plus-context prompt on the service's first turn and an
        incremental one afterwards -- per service, so a rotation target
        that never saw the project still receives everything.
        """
        if self.mode == "chat":
            return user_query
        if not self.sent_files.get(service):
            return self.build_initial_prompt(user_query, service)
        return self.build_followup_prompt(user_query, service)
