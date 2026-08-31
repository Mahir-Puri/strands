"""The orchestrator: the thing that actually runs a plan.

It walks the task graph, respecting dependencies, and hands each ready task
to the right agent. Three behaviours matter here and are the reason this is
its own file:

1. Dependency-aware scheduling. A task only runs once its upstream tasks are
   DONE. If an upstream task FAILED, dependents are SKIPPED, not run on
   partial data.

2. Bounded retries. A failing task is retried up to max_task_attempts. Each
   attempt is logged. When the budget is spent the task is marked FAILED and
   the run keeps going. One flaky task should not sink the whole audit.

3. Context assembly. Before an agent runs, the orchestrator gathers the
   results of exactly the tasks it depended on and passes them in. Agents
   never reach into global state to find their inputs.

This is a plan-execute loop rather than a single mega-prompt. It is more
code, but every step is inspectable and the failure behaviour is explicit.
"""

from __future__ import annotations

from typing import Any

from .agents import (
    Agent,
    CodeReaderAgent,
    GitHubWriterAgent,
    VulnClassifierAgent,
)
from .config import Settings
from .llm import LLMClient
from .memory import Memory
from .planner import Planner
from .retry import RetryError, RetryPolicy, with_retry
from .schemas import AgentType, Plan, PlanRevision, RunReport, Task, TaskStatus
from .supervisor import Supervisor
from .tools.base import ToolRegistry


class Orchestrator:
    def __init__(
        self,
        settings: Settings,
        memory: Memory,
        llm: LLMClient,
        agents: dict[AgentType, Agent],
        supervisor: Supervisor | None = None,
    ):
        self.settings = settings
        self.memory = memory
        self.llm = llm
        self.agents = agents
        self.planner = Planner(llm, memory)
        # Supervisor is optional so the plain workflow behaviour (plan once,
        # execute, done) is still available and still testable on its own.
        self.supervisor = supervisor

    def run(self, goal: str, run_id: str) -> RunReport:
        plan = self.planner.plan(goal)
        self._execute(plan)
        report = RunReport(
            run_id=run_id,
            goal=goal,
            plan=plan,
            findings=self.memory.findings(),
            audit=self.memory.audit.all(),
            completed=all(
                t.status in (TaskStatus.DONE, TaskStatus.SKIPPED) for t in plan.tasks
            ),
        )
        self.memory.audit.record(
            kind="run_complete",
            actor="orchestrator",
            summary=f"run {run_id} finished, {len(report.findings)} findings",
            completed=report.completed,
        )
        return report

    def _execute(self, plan: Plan) -> None:
        # Run one wave of unblocked tasks, then let the supervisor look at the
        # result and possibly revise the plan, then repeat. Without a
        # supervisor this collapses back to "run every wave until none remain".
        #
        # The review budget counts every supervisor look, not just the ones
        # that changed something. That keeps cost predictable: a run makes at
        # most max_replans supervisor calls no matter how large the plan grows.
        # The tradeoff is that a run which needs to adapt late, after the budget
        # is spent, will not. Predictable spend is worth more than unbounded
        # adaptability for something you actually run.
        reviews_used = 0
        while True:
            ready = plan.ready_tasks()
            if ready:
                for task in ready:
                    self._run_task(plan, task)

            if self._can_replan(reviews_used):
                reviews_used += 1
                revision = self.supervisor.review(plan)  # type: ignore[union-attr]
                if self._apply_revision(plan, revision):
                    continue

            if not ready:
                break

        # Anything still PENDING is blocked by a failed or skipped dependency.
        for task in plan.tasks:
            if task.status is TaskStatus.PENDING:
                task.status = TaskStatus.SKIPPED
                task.error = "blocked by an upstream task that did not complete"
                self.memory.audit.record(
                    kind="task_skipped",
                    actor="orchestrator",
                    summary=f"task {task.id} skipped, upstream did not complete",
                    task_id=task.id,
                )

    def _can_replan(self, reviews_used: int) -> bool:
        return (
            self.supervisor is not None
            and self.settings.replan_enabled
            and reviews_used < self.settings.max_replans
        )

    def _apply_revision(self, plan: Plan, revision: PlanRevision) -> bool:
        """Fold a supervisor revision into the plan. Returns True if anything changed."""
        if revision.is_noop():
            return False

        for task in revision.add_tasks:
            plan.tasks.append(task)
            self.memory.audit.record(
                kind="task_added",
                actor="supervisor",
                summary=f"added {task.agent.value} task {task.id}: {task.description[:60]}",
                task_id=task.id,
            )

        for task_id in revision.cancel_task_ids:
            task = plan.task_by_id(task_id)
            if task is not None and task.status is TaskStatus.PENDING:
                task.status = TaskStatus.SKIPPED
                task.error = "cancelled by supervisor during replanning"
                self.memory.audit.record(
                    kind="task_cancelled",
                    actor="supervisor",
                    summary=f"cancelled task {task_id}",
                    task_id=task_id,
                )

        self.memory.audit.record(
            kind="replan_applied",
            actor="supervisor",
            summary=revision.reasoning[:100] or "plan revised",
        )
        return bool(revision.add_tasks or revision.cancel_task_ids)

    def _run_task(self, plan: Plan, task: Task) -> None:
        agent = self.agents.get(task.agent)
        if agent is None:
            task.status = TaskStatus.FAILED
            task.error = f"no agent registered for type {task.agent.value}"
            return

        context = self._context_for(plan, task)
        task.status = TaskStatus.RUNNING
        policy = RetryPolicy(attempts=self.settings.max_task_attempts)

        def _attempt() -> dict[str, Any]:
            task.attempts += 1
            return agent.run(task, context)

        def _on_retry(attempt: int, exc: BaseException) -> None:
            self.memory.audit.record(
                kind="task_retry",
                actor="orchestrator",
                summary=f"task {task.id} attempt {attempt} failed: {type(exc).__name__}",
                task_id=task.id,
                error=str(exc),
            )

        try:
            task.result = with_retry(_attempt, policy, on_retry=_on_retry)
            task.status = TaskStatus.DONE
        except RetryError as err:
            task.status = TaskStatus.FAILED
            task.error = str(err.last)
            self.memory.audit.record(
                kind="task_failed",
                actor="orchestrator",
                summary=f"task {task.id} failed after {task.attempts} attempts",
                task_id=task.id,
                error=str(err.last),
            )

    def _context_for(self, plan: Plan, task: Task) -> dict[str, Any]:
        """Collect the results of the tasks this one depends on."""
        context: dict[str, Any] = {}
        for dep_id in task.depends_on:
            dep = plan.task_by_id(dep_id)
            if dep is not None and dep.result is not None:
                context[dep_id] = dep.result
        return context


def build_orchestrator(
    settings: Settings,
    memory: Memory,
    repo_root: str,
    github_repo: str | None = None,
    llm: LLMClient | None = None,
) -> Orchestrator:
    """Wire everything together for one run.

    Each agent gets its own tool registry holding only the tools it is
    allowed to touch. The code reader cannot open issues; the writer cannot
    read arbitrary files. Least privilege, enforced by what is in the
    registry rather than by asking the model nicely.

    llm can be injected. That is the seam replay uses: hand in a client that
    replays recorded responses instead of calling the real model, and the
    exact same orchestrator and agents run deterministically.
    """

    from .tools import (
        register_filesystem_tools,
        register_github_tools,
        register_vulnerability_tools,
    )

    llm = llm or LLMClient(settings, memory)

    reader_tools = ToolRegistry()
    register_filesystem_tools(reader_tools, repo_root)

    classifier_tools = ToolRegistry()
    register_filesystem_tools(classifier_tools, repo_root)
    register_vulnerability_tools(classifier_tools, memory)

    writer_tools = ToolRegistry()
    register_github_tools(writer_tools, memory, settings, github_repo or "owner/repo")

    agents: dict[AgentType, Agent] = {
        AgentType.CODE_READER: CodeReaderAgent(llm, memory, reader_tools),
        AgentType.VULN_CLASSIFIER: VulnClassifierAgent(llm, memory, classifier_tools),
        AgentType.GITHUB_WRITER: GitHubWriterAgent(llm, memory, writer_tools),
    }
    supervisor = Supervisor(llm, memory) if settings.replan_enabled else None
    return Orchestrator(settings, memory, llm, agents, supervisor=supervisor)
