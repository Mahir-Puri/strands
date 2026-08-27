"""Shared memory and the audit trail.

Two ideas live here.

The audit trail is append-only. Nothing ever edits or deletes a record.
That is what makes a run replayable and is the honest version of
"explainable AI": you can read exactly what happened in order.

The scratchpad is scoped. Each agent gets a namespaced view so a sub-agent
cannot accidentally read or clobber another agent's working state. This
keeps contexts small, which cuts token cost and, more importantly, cuts the
hallucination you get when you dump everything into every prompt.
"""

from __future__ import annotations

import threading
from collections.abc import Iterator
from typing import Any

from .schemas import AuditRecord, Finding


class AuditTrail:
    """Append-only log of everything that happened during a run."""

    def __init__(self) -> None:
        self._records: list[AuditRecord] = []
        self._lock = threading.Lock()

    def record(self, kind: str, actor: str, summary: str, **data: Any) -> AuditRecord:
        rec = AuditRecord(kind=kind, actor=actor, summary=summary, data=data)
        with self._lock:
            self._records.append(rec)
        return rec

    def all(self) -> list[AuditRecord]:
        with self._lock:
            return list(self._records)

    def __iter__(self) -> Iterator[AuditRecord]:
        return iter(self.all())

    def __len__(self) -> int:
        with self._lock:
            return len(self._records)


class Scratchpad:
    """A namespaced key-value store shared across agents.

    Agents read and write under their own namespace by default. Handing one
    agent's output to another is done on purpose by the orchestrator, not by
    accident through a shared global dict.
    """

    def __init__(self) -> None:
        self._data: dict[str, dict[str, Any]] = {}
        self._lock = threading.Lock()

    def put(self, namespace: str, key: str, value: Any) -> None:
        with self._lock:
            self._data.setdefault(namespace, {})[key] = value

    def get(self, namespace: str, key: str, default: Any = None) -> Any:
        with self._lock:
            return self._data.get(namespace, {}).get(key, default)

    def namespace(self, namespace: str) -> dict[str, Any]:
        with self._lock:
            return dict(self._data.get(namespace, {}))


class Memory:
    """Bundles the audit trail, the scratchpad, and the running findings list."""

    def __init__(self) -> None:
        self.audit = AuditTrail()
        self.scratch = Scratchpad()
        self._findings: list[Finding] = []
        self._lock = threading.Lock()

    def add_finding(self, finding: Finding) -> None:
        with self._lock:
            self._findings.append(finding)
        self.audit.record(
            kind="finding",
            actor="memory",
            summary=f"{finding.severity.value}: {finding.title} ({finding.file})",
            finding_id=finding.id,
        )

    def findings(self) -> list[Finding]:
        with self._lock:
            return list(self._findings)
