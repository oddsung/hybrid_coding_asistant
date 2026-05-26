"""Risk classification and execution of shell commands proposed by the LLM.

The LLM's output is untrusted. Commands are screened against two pattern sets
before they are ever offered to the user: ``BLOCKED_PATTERNS`` are refused
outright, ``WARN_PATTERNS`` are flagged so the user can make an informed call.
"""
import re
import subprocess
from typing import List, Tuple

# Destructive patterns that are refused outright -- never executed.
BLOCKED_PATTERNS: List[Tuple[str, str]] = [
    (r"\brm\s+-[a-z]*r[a-z]*f?\s+/(?:\s|$|\*)", "recursive delete of the filesystem root"),
    (r"\brm\s+-[a-z]*r[a-z]*f?\s+~(?:/\s*)?(?:\s|$)", "recursive delete of the home directory"),
    (r":\s*\(\)\s*\{.*\}\s*;\s*:", "fork bomb"),
    (r"\bmkfs\.", "formats a filesystem"),
    (r"\bdd\b.*\bof=/dev/", "raw write to a device"),
    (r">\s*/dev/sd[a-z]", "raw write to a disk device"),
]

# Risky-but-legitimate patterns: warned about, not blocked.
WARN_PATTERNS: List[Tuple[str, str]] = [
    (r"\bsudo\b", "runs with elevated privileges"),
    (r"\b(?:curl|wget)\b[^\n]*\|\s*(?:ba|z)?sh\b", "pipes a remote script straight into a shell"),
    (r"\brm\s+-[a-z]*r", "recursively deletes files"),
    (r">\s*/etc/", "writes into a system directory"),
    (r"\bchmod\s+-?R?\s*777\b", "makes files world-writable"),
    (r"\bgit\b[^\n]*\bpush\b", "pushes to a remote repository"),
]


def classify_command(cmd: str) -> Tuple[bool, List[str], List[str]]:
    """Screen a command.

    Returns ``(blocked, blocked_reasons, warnings)``.
    """
    blocked_reasons = [reason for pat, reason in BLOCKED_PATTERNS if re.search(pat, cmd)]
    warnings = [reason for pat, reason in WARN_PATTERNS if re.search(pat, cmd)]
    return (bool(blocked_reasons), blocked_reasons, warnings)


def run_command(cmd: str, cwd: str) -> int:
    """Run ``cmd`` in ``cwd`` via the shell. Returns the process exit code."""
    result = subprocess.run(cmd, shell=True, cwd=cwd)
    return result.returncode
