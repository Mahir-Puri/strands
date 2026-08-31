"""Tool tests: filesystem sandboxing, finding recording, github dry-run."""

from __future__ import annotations

from pathlib import Path

import pytest

from strands.config import Settings
from strands.memory import Memory
from strands.tools.base import ToolRegistry
from strands.tools.filesystem import SafeRoot, register_filesystem_tools
from strands.tools.github import register_github_tools
from strands.tools.vulnerability import register_vulnerability_tools


def test_saferoot_blocks_escape(tmp_path: Path):
    safe = SafeRoot(tmp_path)
    (tmp_path / "inside.py").write_text("x = 1\n")
    assert safe.resolve("inside.py").name == "inside.py"
    with pytest.raises(PermissionError):
        safe.resolve("../../etc/passwd")


def test_list_and_read_files(tmp_path: Path):
    (tmp_path / "a.py").write_text("print(1)\n")
    (tmp_path / "note.png").write_bytes(b"\x89PNG")  # ignored, not source
    reg = ToolRegistry()
    register_filesystem_tools(reg, tmp_path)
    execs = reg.executors()

    listing = execs["list_files"]({})
    assert "a.py" in listing["files"]
    assert "note.png" not in listing["files"]

    read = execs["read_file"]({"path": "a.py"})
    assert "print(1)" in read["content"]
    assert read["content"].strip().startswith("1")  # line numbers present


def test_read_missing_file_returns_error(tmp_path: Path):
    reg = ToolRegistry()
    register_filesystem_tools(reg, tmp_path)
    out = reg.executors()["read_file"]({"path": "nope.py"})
    assert "error" in out


def test_record_finding_writes_to_memory():
    mem = Memory()
    reg = ToolRegistry()
    register_vulnerability_tools(reg, mem)
    out = reg.executors()["record_finding"](
        {
            "file": "db.py",
            "line": 5,
            "severity": "critical",
            "title": "SQLi",
            "detail": "f-string into SQL",
            "cwe": "CWE-89",
            "confidence": 0.9,
        }
    )
    assert out["recorded"] is True
    assert len(mem.findings()) == 1
    assert mem.findings()[0].cwe == "CWE-89"


def test_github_tool_defaults_to_dry_run():
    mem = Memory()
    settings = Settings(
        anthropic_api_key="",
        model="m",
        planner_model="m",
        max_task_attempts=1,
        max_agent_steps=1,
        replan_enabled=False,
        max_replans=3,
        github_token="",
        github_write_enabled=False,
        request_timeout_s=1.0,
    )
    reg = ToolRegistry()
    register_github_tools(reg, mem, settings, "owner/repo")
    out = reg.executors()["open_issue"]({"title": "t", "body": "b"})
    assert out["created"] is False
    assert out["dry_run"] is True
    # dry runs are still audited so you can see what would have happened
    assert any(r.kind == "github_dry_run" for r in mem.audit.all())


def test_registry_rejects_duplicate_tool():
    reg = ToolRegistry()

    @reg.add(name="dup", description="d", input_schema={"type": "object", "properties": {}})
    def _one():
        return 1

    with pytest.raises(ValueError):

        @reg.add(name="dup", description="d", input_schema={"type": "object", "properties": {}})
        def _two():
            return 2


def test_registry_subset_scopes_tools():
    reg = ToolRegistry()
    reg.add(name="keep", description="d", input_schema={"type": "object", "properties": {}})(lambda: 1)
    reg.add(name="drop", description="d", input_schema={"type": "object", "properties": {}})(lambda: 2)
    scoped = reg.subset(["keep"])
    assert "keep" in scoped
    assert "drop" not in scoped
