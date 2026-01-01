import os
import platform
import fnmatch
from pathlib import Path
from typing import List, Optional

class ContextManager:
    def __init__(self, root_path: str = "."):
        self.root_path = Path(root_path).resolve()
        self.ignore_patterns = self._load_gitignore()
        # Default ignore patterns
        self.ignore_patterns.extend([
            ".git", "__pycache__", ".DS_Store", "venv", "node_modules", 
            "*.pyc", "user_data", ".gemini"
        ])

    def _load_gitignore(self) -> List[str]:
        gitignore_path = self.root_path / ".gitignore"
        patterns = []
        if gitignore_path.exists():
            with open(gitignore_path, "r") as f:
                for line in f:
                    line = line.strip()
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
            
            # Check depth
            current_depth = len(Path(root).relative_to(self.root_path).parts)
            if current_depth > max_depth:
                del dirs[:] # Stop recursing
                continue

            for file in files:
                file_path = Path(root) / file
                if not self._is_ignored(file_path):
                    found_files.append(file_path)
        
        return sorted(found_files)

    def get_context_string(self, files: List[Path]) -> str:
        context_parts = []
        context_parts.append(f"Project Root: {self.root_path}")
        context_parts.append("--- Files Content ---")
        
        for file_path in files:
            try:
                # Limit file size reading (e.g., skip large files or restrict chars)
                if file_path.stat().st_size > 50 * 1024:
                    context_parts.append(f"\n[FILE: {file_path.relative_to(self.root_path)}] (Skipped: >50KB)")
                    continue

                with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
                    content = f.read()
                    context_parts.append(f"\n[FILE: {file_path.relative_to(self.root_path)}]\n{content}")
            except Exception as e:
                context_parts.append(f"\n[FILE: {file_path.relative_to(self.root_path)}] (Error reading: {e})")
        
        return "\n".join(context_parts)

    def build_prompt(self, user_query: str) -> str:
        # Simple strategy: scan all (filtered) files and dump content
        files = self.scan_files()
        context_str = self.get_context_string(files)
        
        system_info = f"OS: {platform.system()} {platform.release()}\nCurrent Directory: {self.root_path}"
        
        prompt = (
            "You are an AI coding assistant. Below is the current project context.\n"
            f"{system_info}\n\n"
            "IMPORTANT INSTRUCTIONS:\n"
            "1. If you generate code for a new file, YOU MUST provide the shell command to create it inside a ` ```bash ` block.\n"
            "   Example:\n"
            "   ```bash\n"
            "   cat << 'EOF' > filename.py\n"
            "   ...\n"
            "   EOF\n"
            "   ```\n"
            "2. If modifying an existing file, provide the diff or the full file content with clear instructions.\n"
            "3. DO NOT use ` ```bash ` for program output examples. Use ` ```text ` instead.\n"
            "4. BE CONCISE. Do not include conversational text, introductions, or 'Here is the code' statements. Put necessary explanations as COMMENTS inside the code.\n"
            "5. Avoid standard endings like 'Let me know if you need anything else'. Just provide the code/commands.\n"
            "6. Answer in the requested language (Korean implied if query is Korean).\n\n"
            "--- Project Files ---\n"
            f"{context_str}\n\n"
            "--- End of Context ---\n\n"
            f"User Question: {user_query}"
        )
        return prompt
