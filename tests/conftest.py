"""Shared test fixtures.

The key idea: a FakeLLM that returns scripted responses so the whole
pipeline can be tested end to end without an API key, a network, or a cent
spent. The orchestrator, planner, retry logic, memory, and tool scoping are
all exercised against this fake. Only the real Anthropic transport is not,
which is the right line to draw for unit tests.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest

from strands.config import Settings
from strands.memory import Memory


@dataclass
class _Block:
    type: str
    text: str = ""
    name: str = ""
    id: str = ""
    input: dict[str, Any] = field(default_factory=dict)


@dataclass
class _Response:
    content: list[_Block]
    stop_reason: str = "end_turn"


class FakeLLM:
    """Stands in for LLMClient. Scripted, deterministic, offline.

    You hand it a list of "turns". Each turn is either a plain string (final
    text answer) or a list of tool-call dicts. complete() and run_with_tools()
    pull from the script in order.
    """

    def __init__(self, settings: Settings, memory: Memory, script: list[Any]):
        self.settings = settings
        self.memory = memory
        self._script = list(script)
        self.calls: list[dict[str, Any]] = []

    def _next(self) -> Any:
        if not self._script:
            return "done"
        return self._script.pop(0)

    def complete(self, *, system, messages, model=None, tools=None, max_tokens=2048, actor="llm"):
        self.calls.append({"actor": actor, "system": system})
        item = self._next()
        if isinstance(item, str):
            return _Response(content=[_Block(type="text", text=item)])
        # a list of tool calls
        blocks = [
            _Block(type="tool_use", name=c["name"], id=f"tu_{i}", input=c.get("input", {}))
            for i, c in enumerate(item)
        ]
        return _Response(content=blocks, stop_reason="tool_use")

    def run_with_tools(self, *, system, user_prompt, tools, executors, model=None, actor="agent", max_steps=None):
        # Replay scripted turns, actually invoking the executors so tool side
        # effects (like recording a finding) really happen.
        while True:
            item = self._next()
            if isinstance(item, str):
                return item
            for call in item:
                executor = executors.get(call["name"])
                if executor is not None:
                    executor(call.get("input", {}))


@pytest.fixture
def settings() -> Settings:
    return Settings(
        anthropic_api_key="test-key",
        model="fake-model",
        planner_model="fake-model",
        max_task_attempts=3,
        max_agent_steps=6,
        replan_enabled=False,
        max_replans=3,
        github_token="",
        github_write_enabled=False,
        request_timeout_s=5.0,
    )


@pytest.fixture
def memory() -> Memory:
    return Memory()


@pytest.fixture
def sample_repo(tmp_path: Path) -> Path:
    (tmp_path / "app.py").write_text(
        "q = 'SELECT * FROM t WHERE x=' + user_input\n"
        "import os\nos.system('ping ' + host)\n"
    )
    (tmp_path / "safe.py").write_text("def add(a, b):\n    return a + b\n")
    return tmp_path
