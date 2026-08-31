"""Orchestrator tests.

These drive the whole plan-execute loop with scripted agents so we can check
scheduling, dependency handling, retries, and skip-on-failure behaviour
without any network.
"""

from __future__ import annotations

from strands.orchestrator import Orchestrator
from strands.schemas import AgentType, Plan, Task, TaskStatus


class ScriptedAgent:
    """An agent whose run() does whatever the test tells it to."""

    def __init__(self, agent_type, behaviour):
        self.agent_type = agent_type
        self._behaviour = behaviour
        self.seen_context = []

    def run(self, task, context):
        self.seen_context.append(context)
        return self._behaviour(task, context)


def _orch(settings, memory, agents):
    class _StubLLM:
        def __init__(self):
            self.settings = settings

    orch = Orchestrator(settings, memory, _StubLLM(), agents)
    return orch


def test_runs_tasks_in_dependency_order(settings, memory):
    order = []

    def make(name):
        def behave(task, ctx):
            order.append(name)
            return {"summary": name}

        return behave

    agents = {
        AgentType.CODE_READER: ScriptedAgent(AgentType.CODE_READER, make("read")),
        AgentType.VULN_CLASSIFIER: ScriptedAgent(AgentType.VULN_CLASSIFIER, make("classify")),
    }
    orch = _orch(settings, memory, agents)

    read = Task(agent=AgentType.CODE_READER, description="read")
    classify = Task(agent=AgentType.VULN_CLASSIFIER, description="classify", depends_on=[read.id])
    plan = Plan(goal="g", tasks=[classify, read])  # deliberately out of order

    orch._execute(plan)
    assert order == ["read", "classify"]
    assert all(t.status is TaskStatus.DONE for t in plan.tasks)


def test_downstream_context_receives_upstream_result(settings, memory):
    classifier = ScriptedAgent(AgentType.VULN_CLASSIFIER, lambda t, c: {"summary": "ok"})
    agents = {
        AgentType.CODE_READER: ScriptedAgent(AgentType.CODE_READER, lambda t, c: {"summary": "shortlist"}),
        AgentType.VULN_CLASSIFIER: classifier,
    }
    orch = _orch(settings, memory, agents)

    read = Task(agent=AgentType.CODE_READER, description="read")
    classify = Task(agent=AgentType.VULN_CLASSIFIER, description="classify", depends_on=[read.id])
    plan = Plan(goal="g", tasks=[read, classify])

    orch._execute(plan)
    # the classifier's context should carry the reader's result under its id
    assert classifier.seen_context[0][read.id]["summary"] == "shortlist"


def test_failed_task_skips_its_dependents(settings, memory):
    def boom(task, ctx):
        raise RuntimeError("agent exploded")

    agents = {
        AgentType.CODE_READER: ScriptedAgent(AgentType.CODE_READER, boom),
        AgentType.VULN_CLASSIFIER: ScriptedAgent(AgentType.VULN_CLASSIFIER, lambda t, c: {"summary": "x"}),
    }
    orch = _orch(settings, memory, agents)

    read = Task(agent=AgentType.CODE_READER, description="read")
    classify = Task(agent=AgentType.VULN_CLASSIFIER, description="classify", depends_on=[read.id])
    plan = Plan(goal="g", tasks=[read, classify])

    orch._execute(plan)
    assert read.status is TaskStatus.FAILED
    assert classify.status is TaskStatus.SKIPPED
    # the failing task was retried up to the configured budget
    assert read.attempts == settings.max_task_attempts


def test_flaky_task_recovers_within_budget(settings, memory):
    state = {"n": 0}

    def flaky(task, ctx):
        state["n"] += 1
        if state["n"] < 2:
            raise RuntimeError("transient")
        return {"summary": "recovered"}

    agents = {AgentType.CODE_READER: ScriptedAgent(AgentType.CODE_READER, flaky)}
    orch = _orch(settings, memory, agents)
    read = Task(agent=AgentType.CODE_READER, description="read")
    plan = Plan(goal="g", tasks=[read])

    orch._execute(plan)
    assert read.status is TaskStatus.DONE
    assert read.attempts == 2
