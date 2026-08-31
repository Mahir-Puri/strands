"""The planner.

Given a plain-language goal, it produces a task graph: which agents run, in
what order, and what depends on what. I kept the planner separate from the
agents that do the work on purpose. Planning is a different skill from
execution, and pulling it out means I can reason about (and test) the
decomposition without running a single tool.

The planner asks the model for JSON, but it does not trust the model to get
it right. Whatever comes back is validated against the schema, and if the
model returns garbage there is a hand-written fallback plan so a run never
dies just because the JSON was malformed.
"""

from __future__ import annotations

import json

from .llm import LLMClient
from .memory import Memory
from .schemas import AgentType, Plan, Task

_PLANNER_SYSTEM = (
    "You are the planner for a multi-agent code-security pipeline. You break a "
    "goal into an ordered list of tasks, each assigned to exactly one agent.\n\n"
    "Available agents:\n"
    "- code_reader: surveys a repo read-only and shortlists suspicious locations.\n"
    "- vuln_classifier: reads flagged code and records structured findings.\n"
    "- github_writer: turns recorded findings into GitHub issues.\n\n"
    "Rules:\n"
    "- Return ONLY a JSON object, no prose, no markdown fences.\n"
    "- Shape: {\"tasks\": [{\"id\": \"t1\", \"agent\": \"code_reader\", "
    "\"description\": \"...\", \"depends_on\": []}, ...]}\n"
    "- Use short ids like t1, t2, t3.\n"
    "- depends_on lists ids that must finish first. Classification depends on "
    "reading; issue writing depends on classification.\n"
    "- Only include github_writer if the goal actually asks to file or open issues.\n"
    "- Keep it to the fewest tasks that accomplish the goal."
)


class Planner:
    def __init__(self, llm: LLMClient, memory: Memory):
        self.llm = llm
        self.memory = memory

    def plan(self, goal: str) -> Plan:
        raw = self._ask_model(goal)
        tasks = self._parse(raw)
        if not tasks:
            tasks = _fallback_tasks(goal)
            self.memory.audit.record(
                kind="planner_fallback",
                actor="planner",
                summary="model plan was unusable, used the built-in fallback",
            )
        plan = Plan(goal=goal, tasks=tasks)
        self.memory.audit.record(
            kind="plan_created",
            actor="planner",
            summary=f"{len(plan.tasks)} tasks for goal: {goal[:80]}",
            task_ids=[t.id for t in plan.tasks],
        )
        return plan

    def _ask_model(self, goal: str) -> str:
        response = self.llm.complete(
            system=_PLANNER_SYSTEM,
            messages=[{"role": "user", "content": f"Goal: {goal}"}],
            model=self.llm.settings.planner_model,
            max_tokens=1024,
            actor="planner",
        )
        return "\n".join(
            b.text for b in response.content if getattr(b, "type", None) == "text"
        ).strip()

    def _parse(self, raw: str) -> list[Task]:
        text = _strip_fences(raw)
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            return []
        items = data.get("tasks", []) if isinstance(data, dict) else []
        tasks: list[Task] = []
        id_map: dict[str, str] = {}
        # First pass: create tasks and remember how the model's ids map to ours.
        for item in items:
            try:
                agent = AgentType(item["agent"])
            except (KeyError, ValueError):
                continue
            task = Task(agent=agent, description=str(item.get("description", "")).strip())
            model_id = str(item.get("id", task.id))
            id_map[model_id] = task.id
            item["_our_id"] = task.id
            tasks.append(task)
        # Second pass: translate depends_on now that every id is known.
        for item, task in zip([i for i in items if "_our_id" in i], tasks, strict=False):
            deps = item.get("depends_on", []) or []
            task.depends_on = [id_map[d] for d in deps if d in id_map]
        return tasks


def _strip_fences(text: str) -> str:
    text = text.strip()
    if text.startswith("```"):
        # drop the first line (``` or ```json) and any trailing fence
        lines = text.splitlines()
        lines = lines[1:]
        if lines and lines[-1].strip().startswith("```"):
            lines = lines[:-1]
        text = "\n".join(lines)
    return text.strip()


def _fallback_tasks(goal: str) -> list[Task]:
    """A sane default pipeline used when the model's plan cannot be parsed."""
    read = Task(agent=AgentType.CODE_READER, description="Survey the repository and shortlist suspicious code.")
    classify = Task(
        agent=AgentType.VULN_CLASSIFIER,
        description="Analyse the shortlisted code and record confirmed findings.",
        depends_on=[read.id],
    )
    tasks = [read, classify]
    if any(word in goal.lower() for word in ("issue", "issues", "file", "open", "report to github")):
        tasks.append(
            Task(
                agent=AgentType.GITHUB_WRITER,
                description="Open a GitHub issue for each recorded finding.",
                depends_on=[classify.id],
            )
        )
    return tasks
