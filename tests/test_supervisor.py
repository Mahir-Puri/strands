"""Supervisor tests: parsing, validation, and the bounded replan loop.

These drive the supervisor with a stub model so we can pin exactly what it
does with a given revision, and drive the orchestrator with scripted agents
so we can prove a replan actually changes what runs.
"""

from __future__ import annotations

import json
from dataclasses import replace

from strands.orchestrator import Orchestrator
from strands.schemas import AgentType, Plan, Task, TaskStatus
from strands.supervisor import Supervisor


class _StubLLM:
    """Returns a scripted sequence of text responses from complete()."""

    def __init__(self, settings, texts):
        self.settings = settings
        self._texts = list(texts)

    def complete(self, **_):
        text = self._texts.pop(0) if self._texts else "{}"

        class B:
            type = "text"

            def __init__(self, t):
                self.text = t

        class R:
            content = [B(text)]

        return R()


def _revision_json(**kw):
    base = {"continue_as_is": True, "reasoning": "", "add_tasks": [], "cancel_task_ids": []}
    base.update(kw)
    return json.dumps(base)


def test_noop_when_model_says_continue(settings, memory):
    sup = Supervisor(_StubLLM(settings, [_revision_json(continue_as_is=True)]), memory)
    plan = Plan(goal="g", tasks=[Task(agent=AgentType.CODE_READER, description="read")])
    rev = sup.review(plan)
    assert rev.is_noop()


def test_unparseable_review_is_safe_noop(settings, memory):
    sup = Supervisor(_StubLLM(settings, ["not json at all"]), memory)
    plan = Plan(goal="g", tasks=[Task(agent=AgentType.CODE_READER, description="read")])
    rev = sup.review(plan)
    assert rev.is_noop()


def test_cancel_only_applies_to_pending_tasks(settings, memory):
    done = Task(agent=AgentType.CODE_READER, description="read", status=TaskStatus.DONE)
    pending = Task(agent=AgentType.GITHUB_WRITER, description="write")
    plan = Plan(goal="g", tasks=[done, pending])
    sup = Supervisor(
        _StubLLM(settings, [_revision_json(continue_as_is=False, cancel_task_ids=[done.id, pending.id])]),
        memory,
    )
    rev = sup.review(plan)
    # the DONE task is not cancellable, only the pending one survives the filter
    assert rev.cancel_task_ids == [pending.id]


def test_added_task_deps_are_validated(settings, memory):
    existing = Task(agent=AgentType.CODE_READER, description="read", status=TaskStatus.DONE)
    plan = Plan(goal="g", tasks=[existing])
    add = [
        {"id": "n1", "agent": "vuln_classifier", "description": "dig deeper", "depends_on": [existing.id, "ghost"]}
    ]
    sup = Supervisor(_StubLLM(settings, [_revision_json(continue_as_is=False, add_tasks=add)]), memory)
    rev = sup.review(plan)
    assert len(rev.add_tasks) == 1
    # the real dependency is kept, the hallucinated "ghost" id is dropped
    assert rev.add_tasks[0].depends_on == [existing.id]


def test_unknown_agent_in_added_task_is_dropped(settings, memory):
    plan = Plan(goal="g", tasks=[])
    add = [{"id": "n1", "agent": "wizard", "description": "x"}]
    sup = Supervisor(_StubLLM(settings, [_revision_json(continue_as_is=False, add_tasks=add)]), memory)
    rev = sup.review(plan)
    assert rev.add_tasks == []


# --- orchestrator + supervisor integration ---


class ScriptedAgent:
    def __init__(self, agent_type, behaviour):
        self.agent_type = agent_type
        self._behaviour = behaviour

    def run(self, task, context):
        return self._behaviour(task, context)


def _settings_with_replan(settings):
    return replace(settings, replan_enabled=True, max_replans=2)


def test_replan_adds_a_task_that_actually_runs(settings, memory):
    ran = []

    agents = {
        AgentType.CODE_READER: ScriptedAgent(AgentType.CODE_READER, lambda t, c: ran.append("read") or {"summary": "s"}),
        AgentType.VULN_CLASSIFIER: ScriptedAgent(
            AgentType.VULN_CLASSIFIER, lambda t, c: ran.append("classify") or {"summary": "s"}
        ),
    }

    read = Task(agent=AgentType.CODE_READER, description="read")
    plan = Plan(goal="g", tasks=[read])

    # First review adds a classifier task; second review is a no-op.
    add = [{"id": "n1", "agent": "vuln_classifier", "description": "added"}]
    llm = _StubLLM(
        _settings_with_replan(settings),
        [_revision_json(continue_as_is=False, add_tasks=add), _revision_json(continue_as_is=True)],
    )
    sup = Supervisor(llm, memory)
    orch = Orchestrator(_settings_with_replan(settings), memory, llm, agents, supervisor=sup)

    orch._execute(plan)
    assert ran == ["read", "classify"]
    assert any(r.kind == "task_added" for r in memory.audit.all())


def test_replan_is_bounded(settings, memory):
    # A supervisor that always wants to add work must still stop at the cap.
    agents = {
        AgentType.CODE_READER: ScriptedAgent(AgentType.CODE_READER, lambda t, c: {"summary": "s"}),
    }
    read = Task(agent=AgentType.CODE_READER, description="read")
    plan = Plan(goal="g", tasks=[read])

    # Endless "add another reader" reviews. The cap is what saves us.
    add = [{"id": "n", "agent": "code_reader", "description": "again"}]
    always_add = [_revision_json(continue_as_is=False, add_tasks=add) for _ in range(20)]
    cfg = replace(settings, replan_enabled=True, max_replans=2)
    llm = _StubLLM(cfg, always_add)
    sup = Supervisor(llm, memory)
    orch = Orchestrator(cfg, memory, llm, agents, supervisor=sup)

    orch._execute(plan)
    applied = [r for r in memory.audit.all() if r.kind == "replan_applied"]
    assert len(applied) == 2  # capped at max_replans
