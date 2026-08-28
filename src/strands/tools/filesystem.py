"""Read-only filesystem tools scoped to a single root directory.

The code reader agent uses these to walk a target repo. Every path is
resolved and checked against the root before anything is opened, so a model
that asks for ../../etc/passwd gets a polite refusal instead of a leak. The
tools never write. Reading a codebase should not be able to change it.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .base import ToolRegistry

# File extensions worth reading for a security pass. Everything else is
# noise for this use case (images, lockfiles, minified vendor bundles).
_SOURCE_SUFFIXES = {
    ".py", ".js", ".ts", ".tsx", ".jsx", ".java", ".go", ".rb", ".rs",
    ".php", ".c", ".cpp", ".cs", ".sh", ".sql", ".yaml", ".yml", ".env",
    ".cfg", ".ini", ".toml", ".json",
}

_SKIP_DIRS = {".git", "node_modules", "__pycache__", ".venv", "venv", "dist", "build"}

_MAX_BYTES = 60_000  # do not feed a giant generated file into a prompt


class SafeRoot:
    """Confines all access to files under one directory."""

    def __init__(self, root: str | Path):
        self.root = Path(root).resolve()
        if not self.root.is_dir():
            raise NotADirectoryError(f"{self.root} is not a directory")

    def resolve(self, rel: str) -> Path:
        candidate = (self.root / rel).resolve()
        # is_relative_to keeps the model inside the sandbox.
        if not candidate.is_relative_to(self.root):
            raise PermissionError(f"path escapes the target root: {rel}")
        return candidate


def register_filesystem_tools(registry: ToolRegistry, root: str | Path) -> None:
    safe = SafeRoot(root)

    @registry.add(
        name="list_files",
        description=(
            "List source files under the target repository, relative to its root. "
            "Skips vendored and build directories. Use this first to see what "
            "there is before reading anything."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "subdir": {
                    "type": "string",
                    "description": "Optional subdirectory to list, relative to the repo root. Empty means the whole repo.",
                }
            },
        },
    )
    def list_files(subdir: str = "") -> dict[str, Any]:
        base = safe.resolve(subdir) if subdir else safe.root
        found: list[str] = []
        for path in base.rglob("*"):
            if any(part in _SKIP_DIRS for part in path.parts):
                continue
            if path.is_file() and path.suffix in _SOURCE_SUFFIXES:
                found.append(str(path.relative_to(safe.root)))
        return {"root": str(safe.root), "count": len(found), "files": sorted(found)}

    @registry.add(
        name="read_file",
        description=(
            "Read the contents of one source file, relative to the repo root. "
            "Returns numbered lines so findings can reference a line number. "
            "Large files are truncated."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "File path relative to the repo root.",
                }
            },
            "required": ["path"],
        },
    )
    def read_file(path: str) -> dict[str, Any]:
        target = safe.resolve(path)
        if not target.is_file():
            return {"error": f"not a file: {path}"}
        raw = target.read_bytes()[:_MAX_BYTES]
        text = raw.decode("utf-8", errors="replace")
        numbered = "\n".join(f"{i + 1:>4}  {line}" for i, line in enumerate(text.splitlines()))
        return {
            "path": path,
            "truncated": target.stat().st_size > _MAX_BYTES,
            "content": numbered,
        }
