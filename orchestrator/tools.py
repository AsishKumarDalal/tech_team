"""Workspace tools exposed to agents.

Agents no longer paste code into the chat -- they CALL these tools to read,
create, edit, delete and run things inside the project's code directory. This
gives the Code Lead / coders real file-editing ability (create AND edit) and a
shell for builds/tests, exactly as the docs' "workspace tools" require.

All paths are sandboxed under `code_root`; a tool call outside it is rejected.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

from langchain_core.tools import tool


def make_tools(code_root) -> list:
    root = Path(code_root).resolve()

    def _safe(path: str) -> Path:
        p = (root / path).resolve()
        if root not in p.parents and p != root:
            raise ValueError(f"path escapes project: {path}")
        return p

    @tool
    def read_file(path: str) -> str:
        """Read a UTF-8 file inside the project. Returns contents or an error string."""
        try:
            p = _safe(path)
        except ValueError as e:
            return f"ERROR: {e}"
        if not p.exists():
            return f"ERROR: not found: {path}"
        return p.read_text(encoding="utf-8", errors="replace")

    @tool
    def write_file(path: str, content: str) -> str:
        """Create or overwrite a file with `content`. Creates parent dirs."""
        try:
            p = _safe(path)
        except ValueError as e:
            return f"ERROR: {e}"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
        return f"Wrote {p.relative_to(root)} ({len(content)} chars)"

    @tool
    def edit_file(path: str, old_string: str, new_string: str) -> str:
        """Replace the FIRST occurrence of old_string with new_string in a file."""
        try:
            p = _safe(path)
        except ValueError as e:
            return f"ERROR: {e}"
        if not p.exists():
            return f"ERROR: not found: {path}"
        text = p.read_text(encoding="utf-8")
        if old_string not in text:
            return "ERROR: old_string not found (must match exactly, incl. whitespace)"
        text = text.replace(old_string, new_string, 1)
        p.write_text(text, encoding="utf-8")
        return f"Edited {p.relative_to(root)}"

    @tool
    def delete_file(path: str) -> str:
        """Delete a file inside the project."""
        try:
            p = _safe(path)
        except ValueError as e:
            return f"ERROR: {e}"
        if not p.exists():
            return f"ERROR: not found: {path}"
        p.unlink()
        return f"Deleted {p.relative_to(root)}"

    @tool
    def list_files(path: str = ".") -> str:
        """List files under a project path (one per line)."""
        try:
            p = _safe(path)
        except ValueError as e:
            return f"ERROR: {e}"
        if not p.exists():
            return f"ERROR: not found: {path}"
        lines = [str(f.relative_to(root)) for f in sorted(p.rglob("*")) if f.is_file()]
        return "\n".join(lines) or "(empty)"

    @tool
    def run_command(command: str) -> str:
        """Run a shell command in the project dir (120s timeout). Returns rc + stdout/stderr."""
        try:
            r = subprocess.run(
                command, shell=True, cwd=str(root),
                capture_output=True, text=True, timeout=120,
            )
            out = f"rc={r.returncode}\n--- stdout ---\n{r.stdout}\n--- stderr ---\n{r.stderr}"
            return out[-4000:]
        except subprocess.TimeoutExpired:
            return "ERROR: command timed out after 120s"
        except Exception as e:  # noqa: BLE001
            return f"ERROR: {e}"

    return [read_file, write_file, edit_file, delete_file, list_files, run_command]


READ_TOOLS = ("read_file", "list_files")
ALL_TOOLS = ("read_file", "write_file", "edit_file", "delete_file", "list_files", "run_command")
