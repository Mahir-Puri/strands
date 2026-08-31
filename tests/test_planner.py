"""Planner tests: good JSON, fenced JSON, garbage, and dependency remap."""

from __future__ import annotations

import json

from strands.planner import Planner, _fallback_tasks, _strip_fences
from strands.schemas import AgentType


def _planner_returning(text, settings, memory):
    planner = Planner(llm=None, memory=memory)  # llm patched below

    class _Stub:
        def __init__(self):
            self.settings = settings

        def complete(self, **_):
            class B:
                type = "text"
                text = _text

            class R:
                content = [B()]

            return R()

    _text = text
    planner.llm = _Stub()
    return planner


def test_parses_clean_json(settings, memory):
    payload = json.dumps(
        {
            "tasks": [
                {"id": "t1", "agent": "code_reader", "description": "read", "depends_on": []},
                {"id": "t2", "agent": "vuln_classifier", "description": "classify", "depends_on": ["t1"]},
            ]
        }
    )
    plan = _planner_returning(payload, settings, memory).plan("audit it")
    assert len(plan.tasks) == 2
    assert plan.tasks[0].agent is AgentType.CODE_READER
    # dependency ids get remapped to our internal ids, and still point at t1
    assert plan.tasks[1].depends_on == [plan.tasks[0].id]


def test_handles_fenced_json(settings, memory):
    payload = "```json\n" + json.dumps({"tasks": [{"id": "t1", "agent": "code_reader", "description": "x"}]}) + "\n```"
    plan = _planner_returning(payload, settings, memory).plan("audit")
    assert len(plan.tasks) == 1


def test_garbage_falls_back(settings, memory):
    plan = _planner_returning("i am not json", settings, memory).plan("audit and open issues")
    # fallback kicks in and, because the goal mentions issues, includes the writer
    agents = [t.agent for t in plan.tasks]
    assert AgentType.CODE_READER in agents
    assert AgentType.VULN_CLASSIFIER in agents
    assert AgentType.GITHUB_WRITER in agents
    assert any(r.kind == "planner_fallback" for r in memory.audit.all())


def test_fallback_without_issue_keyword_skips_writer():
    tasks = _fallback_tasks("just find problems")
    assert AgentType.GITHUB_WRITER not in [t.agent for t in tasks]


def test_strip_fences_plain_passthrough():
    assert _strip_fences('{"a": 1}') == '{"a": 1}'


def test_unknown_agent_is_dropped(settings, memory):
    payload = json.dumps(
        {"tasks": [{"id": "t1", "agent": "wizard", "description": "x"}, {"id": "t2", "agent": "code_reader", "description": "y"}]}
    )
    plan = _planner_returning(payload, settings, memory).plan("audit")
    assert len(plan.tasks) == 1
    assert plan.tasks[0].agent is AgentType.CODE_READER
