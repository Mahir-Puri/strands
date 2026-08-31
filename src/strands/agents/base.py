"""Base class every sub-agent inherits from.

An agent is a narrow specialist: a system prompt, a scoped set of tools, and
a bounded slice of memory. It does one kind of job. The orchestrator decides
which agent runs when. Agents do not call each other directly, which keeps
the control flow in one place and stops two agents from quietly forming a
loop nobody can see.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from ..llm import LLMClient
from ..memory import Memory
from ..schemas import AgentType, Task
from ..tools.base import ToolRegistry


class Agent(ABC):
    #: Set by each subclass. Used by the orchestrator to route tasks.
    agent_type: AgentType

    def __init__(self, llm: LLMClient, memory: Memory, tools: ToolRegistry):
        self.llm = llm
        self.memory = memory
        self.tools = tools

    @property
    def namespace(self) -> str:
        return self.agent_type.value

    @abstractmethod
    def system_prompt(self) -> str:
        """The role definition handed to the model for this agent."""

    @abstractmethod
    def build_prompt(self, task: Task, context: dict[str, Any]) -> str:
        """Turn the task plus upstream context into the user message."""

    def run(self, task: Task, context: dict[str, Any]) -> dict[str, Any]:
        """Execute one task and return a result dict.

        context carries results from tasks this one depended on. The
        orchestrator assembles it so the agent does not have to go hunting
        through global state.
        """

        self.memory.audit.record(
            kind="agent_start",
            actor=self.namespace,
            summary=f"task {task.id}: {task.description[:80]}",
            task_id=task.id,
        )
        answer = self.llm.run_with_tools(
            system=self.system_prompt(),
            user_prompt=self.build_prompt(task, context),
            tools=self.tools.specs(),
            executors=self.tools.executors(),
            actor=self.namespace,
        )
        self.memory.scratch.put(self.namespace, task.id, answer)
        self.memory.audit.record(
            kind="agent_done",
            actor=self.namespace,
            summary=f"task {task.id} finished",
            task_id=task.id,
        )
        return {"summary": answer}
