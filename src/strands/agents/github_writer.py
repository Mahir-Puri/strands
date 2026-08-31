"""The GitHub writer.

Turns recorded findings into issue drafts and calls open_issue for each. In
the default dry-run setup it produces the exact issues it would file without
touching the repo, which is what you want when you are demoing or testing.
The findings come from shared memory rather than the prompt so the agent is
always writing about issues the system actually recorded, not ones it
imagined mid-sentence.
"""

from __future__ import annotations

from typing import Any

from ..schemas import AgentType, Task
from .base import Agent


class GitHubWriterAgent(Agent):
    agent_type = AgentType.GITHUB_WRITER

    def system_prompt(self) -> str:
        return (
            "You are an agent that writes GitHub issues for confirmed security "
            "findings. You are given the list of findings the pipeline recorded. For "
            "each one, write a clear issue aimed at a maintainer: a specific title, a "
            "body with the file and line, what the risk is, and how to fix it. Call "
            "open_issue once per finding. Do not invent findings that are not in the "
            "list. Keep titles short and specific."
        )

    def build_prompt(self, task: Task, context: dict[str, Any]) -> str:
        findings = self.memory.findings()
        if not findings:
            return (
                f"Task: {task.description}\n\n"
                "There are no recorded findings, so there is nothing to file. Say so "
                "and stop."
            )
        lines = []
        for f in findings:
            loc = f"{f.file}:{f.line}" if f.line else f.file
            lines.append(
                f"- [{f.severity.value}] {f.title} ({loc}) "
                f"cwe={f.cwe or 'n/a'} conf={f.confidence:.2f}\n  {f.detail}\n"
                f"  fix: {f.recommendation or 'not specified'}"
            )
        listing = "\n".join(lines)
        return (
            f"Task: {task.description}\n\n"
            f"Recorded findings:\n\n{listing}\n\n"
            "Open one issue per finding with open_issue."
        )
