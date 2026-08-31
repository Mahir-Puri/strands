"""The code reader.

Its whole job is to survey the target repo and produce a shortlist of files
and spots that a security review should look at closely. It does not judge
severity or write anything. Splitting "find the interesting code" from
"decide if it is actually a vulnerability" keeps each agent's prompt small
and its job easy to check.
"""

from __future__ import annotations

from typing import Any

from ..schemas import AgentType, Task
from .base import Agent


class CodeReaderAgent(Agent):
    agent_type = AgentType.CODE_READER

    def system_prompt(self) -> str:
        return (
            "You are a code reconnaissance agent inside a security review pipeline. "
            "You have read-only tools to list and read files in a target repository. "
            "Your job is to survey the codebase and report the files and specific "
            "locations most worth a close security review: things like raw SQL "
            "string building, shell calls, deserialization, auth checks, secret "
            "handling, and unsafe input flows.\n\n"
            "Work in this order: list the files, read the ones that look relevant, "
            "then summarise. Do not guess about files you have not read. Do not rate "
            "severity and do not invent issues. Produce a concise, grounded shortlist "
            "with file paths and line numbers so the next agent can go straight to them."
        )

    def build_prompt(self, task: Task, context: dict[str, Any]) -> str:
        return (
            f"Task: {task.description}\n\n"
            "Survey the repository and return a shortlist of the locations that most "
            "deserve a security review. For each, give the file path, an approximate "
            "line number, and one sentence on why it caught your attention."
        )
