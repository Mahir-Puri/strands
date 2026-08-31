"""Memory, scratchpad scoping, and audit trail tests."""

from __future__ import annotations

from strands.memory import Memory
from strands.schemas import Finding, Severity


def test_audit_is_append_only_and_ordered():
    mem = Memory()
    mem.audit.record(kind="a", actor="x", summary="first")
    mem.audit.record(kind="b", actor="y", summary="second")
    records = mem.audit.all()
    assert [r.summary for r in records] == ["first", "second"]
    assert len(mem.audit) == 2


def test_scratchpad_namespaces_are_isolated():
    mem = Memory()
    mem.scratch.put("agent_a", "k", "value-a")
    mem.scratch.put("agent_b", "k", "value-b")
    assert mem.scratch.get("agent_a", "k") == "value-a"
    assert mem.scratch.get("agent_b", "k") == "value-b"
    # a namespace cannot see another's keys
    assert mem.scratch.get("agent_a", "missing") is None


def test_adding_finding_records_audit_line():
    mem = Memory()
    finding = Finding(
        file="app.py",
        line=10,
        severity=Severity.HIGH,
        title="SQL injection",
        detail="string built query",
    )
    mem.add_finding(finding)
    assert len(mem.findings()) == 1
    kinds = [r.kind for r in mem.audit.all()]
    assert "finding" in kinds


def test_findings_returns_a_copy():
    mem = Memory()
    mem.add_finding(
        Finding(file="a.py", severity=Severity.LOW, title="t", detail="d")
    )
    got = mem.findings()
    got.clear()
    # mutating the returned list must not empty the store
    assert len(mem.findings()) == 1
