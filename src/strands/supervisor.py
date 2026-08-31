"""The supervisor: the thing that makes this an agent and not a workflow.

A workflow commits to a plan and marches through it no matter what it learns.
An agent notices when reality diverges from the plan and adapts. The
supervisor is where that happens. After each wave of task execution the
orchestrator hands the whole plan, the findings so far, and a slice of the
audit trail to the supervisor and asks one question: given what we now know,
is the rest of this plan still the right thing to do?

Most of the time the answer is yes and this is a no-op. The interesting cases:

- The classifier found nothing, so the queued issue-writing task is pointless
  and gets cancelled.
- The reader turned up a whole category of problem the original plan did not
  anticipate, so a new classification task gets added.
- A task failed in a way that suggests a different approach, so a fallback
  task gets appended.

Two things keep this safe. It is bounded: a run gets at most max_replans
reviews, after which it finishes with whatever plan it has. And it is
validated: new tasks must use known agent types and real dependency ids, or
they are dropped. A confused supervisor cannot invent a runaway plan.
"""

from __future__ import annotations

import json

from .llm import LLMClient
from .memory import Memory
from .schemas import AgentType, Plan, PlanRevision, Task, TaskStatus

_SUPERVISOR_SYSTEM = (
    "You are the supervisor of a multi-agent code-security pipeline. A plan is "
    "already running. You see the goal, what each task has done, what the agents "
    "observed, and the findings recorded so far. You answer one question: given "
    "what we now know, what should happen next to accomplish the goal?\n\n"
    "Available agents:\n"
    "- code_reader: surveys a repo read-only and shortlists suspicious locations.\n"
    "- vuln_classifier: reads flagged code and records structured findings.\n"
    "- github_writer: turns recorded findings into GitHub issues.\n\n"
    "Judge whether the goal is actually met yet, and set goal_met accordingly. "
    "Default to leaving the plan alone when it is on track. Change it only when "
    "there is a clear reason:\n"
    "- Cancel a pending github_writer task if zero findings were recorded.\n"
    "- Add a vuln_classifier task if an agent's observations point at a distinct "
    "area the plan did not cover.\n"
    "- Add a fallback task if something failed and a different angle would help.\n\n"
    "Return ONLY a JSON object, no prose, no markdown fences:\n"
    "{\"goal_met\": true|false, \"continue_as_is\": true|false, "
    "\"reasoning\": \"one sentence\", \"add_tasks\": [{\"id\": \"n1\", "
    "\"agent\": \"vuln_classifier\", \"description\": \"...\", \"depends_on\": []}], "
    "\"cancel_task_ids\": []}\n\n"
    "Only cancel tasks that are still pending. New task ids should not collide "
    "with existing ones. Keep changes minimal."
)


class Supervisor:
    def __init__(self, llm: LLMClient, memory: Memory):
        self.llm = llm
        self.memory = memory

    def review(self, plan: Plan) -> PlanRevision:
        """Look at the running plan and decide whether to revise it."""
        raw = self._ask_model(plan)
        revision = self._parse(raw, plan)
        self.memory.audit.record(
            kind="replan_review",
            actor="supervisor",
            summary=(
                "no change" if revision.is_noop()
                else f"revise: +{len(revision.add_tasks)} -{len(revision.cancel_task_ids)}"
            ),
            reasoning=revision.reasoning,
            goal_met=revision.goal_met,
        )
        return revision

    def _ask_model(self, plan: Plan) -> str:
        response = self.llm.complete(
            system=_SUPERVISOR_SYSTEM,
            messages=[{"role": "user", "content": _state_summary(plan, self.memory)}],
            model=self.llm.settings.planner_model,
            max_tokens=1024,
            actor="supervisor",
        )
        return "\n".join(
            b.text for b in response.content if getattr(b, "type", None) == "text"
        ).strip()

    def _parse(self, raw: str, plan: Plan) -> PlanRevision:
        text = _strip_fences(raw)
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            # Unparseable review is treated as "leave it alone". Failing safe
            # here means a flaky supervisor never derails a good plan.
            return PlanRevision(continue_as_is=True, reasoning="unparseable review, kept plan")

        existing_ids = {t.id for t in plan.tasks}
        add_tasks, id_map = _build_added_tasks(data.get("add_tasks", []) or [], existing_ids)

        # Only pending tasks may be cancelled, and only real ones.
        cancellable = {t.id for t in plan.tasks if t.status is TaskStatus.PENDING}
        cancel_ids = [tid for tid in (data.get("cancel_task_ids", []) or []) if tid in cancellable]

        return PlanRevision(
            continue_as_is=bool(data.get("continue_as_is", True)) and not add_tasks and not cancel_ids,
            reasoning=str(data.get("reasoning", ""))[:300],
            goal_met=data.get("goal_met"),
            add_tasks=add_tasks,
            cancel_task_ids=cancel_ids,
        )


def _build_added_tasks(items: list[dict], existing_ids: set[str]) -> tuple[list[Task], dict[str, str]]:
    """Turn raw task dicts into validated Tasks, remapping their ids.

    Dependencies may point at existing task ids or at other newly-added ones.
    Anything that resolves to neither is dropped, so a dependency the model
    hallucinated cannot wedge a task as permanently unschedulable.
    """
    tasks: list[Task] = []
    id_map: dict[str, str] = {}
    raw_deps: list[list[str]] = []

    for item in items:
        try:
            agent = AgentType(item["agent"])
        except (KeyError, ValueError):
            continue
        task = Task(agent=agent, description=str(item.get("description", "")).strip())
        model_id = str(item.get("id", task.id))
        id_map[model_id] = task.id
        tasks.append(task)
        raw_deps.append(item.get("depends_on", []) or [])

    known = set(existing_ids) | set(id_map.values())
    for task, deps in zip(tasks, raw_deps, strict=False):
        resolved = []
        for dep in deps:
            mapped = id_map.get(dep, dep)  # a newly-added id, or an existing one
            if mapped in known:
                resolved.append(mapped)
        task.depends_on = resolved
    return tasks, id_map


def _state_summary(plan: Plan, memory: Memory) -> str:
    """A compact snapshot of the run for the supervisor to reason over.

    Crucially this includes what the agents observed, not just whether their
    task passed. A revision made on pass/fail alone is barely better than a
    fixed script; a revision made on what the code reader actually saw is the
    part that makes this a real observe-and-adapt loop.
    """
    lines = [f"Goal: {plan.goal}", "", "Tasks so far:"]
    for task in plan.tasks:
        note = ""
        if task.status is TaskStatus.FAILED and task.error:
            note = f"  (error: {task.error[:80]})"
        lines.append(f"- {task.id} [{task.status.value}] {task.agent.value}: {task.description[:70]}{note}")

    # The agents' own observations from completed tasks, trimmed so this stays
    # a summary and not a transcript.
    observations = [
        (task.agent.value, task.result["summary"])
        for task in plan.tasks
        if task.status is TaskStatus.DONE
        and isinstance(task.result, dict)
        and task.result.get("summary")
    ]
    if observations:
        lines.append("")
        lines.append("What the agents observed:")
        for agent_name, summary in observations[-4:]:
            lines.append(f"- {agent_name}: {str(summary)[:400]}")

    findings = memory.findings()
    lines.append("")
    lines.append(f"Findings recorded so far: {len(findings)}")
    for f in findings[:10]:
        lines.append(f"- [{f.severity.value}] {f.title} ({f.file})")

    return "\n".join(lines)


def _strip_fences(text: str) -> str:
    text = text.strip()
    if text.startswith("```"):
        lines = text.splitlines()[1:]
        if lines and lines[-1].strip().startswith("```"):
            lines = lines[:-1]
        text = "\n".join(lines)
    return text.strip()
