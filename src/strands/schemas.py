"""Data models used across the whole system.

Everything that moves between the planner, the orchestrator, and the
sub-agents is one of these. Keeping them in one place means there is a
single source of truth for what a "task" or a "finding" actually is.
"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, Field


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _new_id() -> str:
    return uuid4().hex[:12]


class AgentType(str, Enum):
    """Which specialist handles a task.

    The planner picks one of these for every subtask it produces. If you
    add a new sub-agent you add it here and register it in the orchestrator.
    """

    CODE_READER = "code_reader"
    VULN_CLASSIFIER = "vuln_classifier"
    GITHUB_WRITER = "github_writer"


class TaskStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"
    SKIPPED = "skipped"


class Severity(str, Enum):
    INFO = "info"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class Task(BaseModel):
    """One unit of work assigned to a single agent.

    depends_on lets the planner express ordering. A task only runs once
    every id in depends_on has reached DONE. If a dependency FAILED, the
    orchestrator marks this task SKIPPED instead of running it blind.
    """

    id: str = Field(default_factory=_new_id)
    agent: AgentType
    description: str
    depends_on: list[str] = Field(default_factory=list)
    status: TaskStatus = TaskStatus.PENDING
    result: dict[str, Any] | None = None
    error: str | None = None
    attempts: int = 0


class Plan(BaseModel):
    """The full decomposition of a goal into ordered tasks."""

    id: str = Field(default_factory=_new_id)
    goal: str
    tasks: list[Task] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=_now)

    def task_by_id(self, task_id: str) -> Task | None:
        for task in self.tasks:
            if task.id == task_id:
                return task
        return None

    def ready_tasks(self) -> list[Task]:
        """Tasks whose dependencies are all satisfied and are still pending."""
        ready = []
        for task in self.tasks:
            if task.status is not TaskStatus.PENDING:
                continue
            deps = [self.task_by_id(dep) for dep in task.depends_on]
            if any(dep is None for dep in deps):
                # A dependency that does not exist is a planner mistake. Treat
                # it as unsatisfiable so we do not spin forever.
                continue
            if all(dep.status is TaskStatus.DONE for dep in deps):  # type: ignore[union-attr]
                ready.append(task)
        return ready


class PlanRevision(BaseModel):
    """The supervisor's verdict after looking at a plan mid-run.

    continue_as_is is the common case: the plan is still fine, do nothing.
    Otherwise the supervisor can append new tasks (say, a deeper look at
    something a reader turned up) or cancel pending tasks that no longer make
    sense (say, skip issue-writing because nothing was found).

    goal_met is the supervisor's read on whether the original goal has
    actually been accomplished yet. It does not drive control flow on its own,
    but it is recorded, so the audit trail shows the moment the agent believed
    it was done. That is what makes the loop goal-directed rather than just a
    script that runs to the end of its task list.
    """

    continue_as_is: bool = True
    reasoning: str = ""
    goal_met: bool | None = None
    add_tasks: list[Task] = Field(default_factory=list)
    cancel_task_ids: list[str] = Field(default_factory=list)

    def is_noop(self) -> bool:
        return self.continue_as_is and not self.add_tasks and not self.cancel_task_ids


class Finding(BaseModel):
    """A single security issue the system believes it found."""

    id: str = Field(default_factory=_new_id)
    file: str
    line: int | None = None
    severity: Severity
    title: str
    detail: str
    cwe: str | None = None
    recommendation: str | None = None
    confidence: float = 0.5


class AuditRecord(BaseModel):
    """One line in the audit trail.

    Every meaningful thing the system does gets one of these: a plan being
    made, a task starting, a tool call, an LLM response, a retry, a failure.
    Together they let you replay a run start to finish without guessing.
    """

    id: str = Field(default_factory=_new_id)
    ts: datetime = Field(default_factory=_now)
    kind: str
    actor: str
    summary: str
    data: dict[str, Any] = Field(default_factory=dict)


class RunReport(BaseModel):
    """What a caller gets back at the end of a run."""

    run_id: str
    goal: str
    plan: Plan
    findings: list[Finding] = Field(default_factory=list)
    audit: list[AuditRecord] = Field(default_factory=list)
    completed: bool = False
