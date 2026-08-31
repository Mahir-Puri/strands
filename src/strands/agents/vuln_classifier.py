"""The vulnerability classifier.

Takes the code reader's shortlist, reads the actual code at those spots, and
records structured findings through the record_finding tool. It also has the
filesystem tools so it can confirm things for itself rather than trusting the
shortlist blindly. The rule I gave it in the prompt is the important part:
confirm before recording, and skip anything you cannot actually see in the
code. That is what keeps the false-positive rate down.
"""

from __future__ import annotations

from typing import Any

from ..schemas import AgentType, Task
from .base import Agent


class VulnClassifierAgent(Agent):
    agent_type = AgentType.VULN_CLASSIFIER

    def system_prompt(self) -> str:
        return (
            "You are a vulnerability analysis agent. You are given a shortlist of "
            "suspicious locations in a repository. You have read-only file tools and "
            "one tool to record a finding.\n\n"
            "For each candidate: read the real code, decide whether it is genuinely a "
            "security issue, and if it is, call record_finding once with an accurate "
            "severity, a CWE id when you know it, and a concrete fix. If the code is "
            "actually safe, or you cannot confirm the issue from what you can read, do "
            "not record anything. A clean pass with three real findings beats a noisy "
            "pass with ten guesses. Be honest about confidence.\n\n"
            "When you are done, briefly summarise what you recorded and what you "
            "checked but dismissed."
        )

    def build_prompt(self, task: Task, context: dict[str, Any]) -> str:
        shortlist = _gather_shortlist(context)
        intro = f"Task: {task.description}\n\n"
        if shortlist:
            intro += "The reconnaissance agent flagged these locations:\n\n" + shortlist + "\n\n"
        else:
            intro += (
                "No shortlist was provided. Survey the repository yourself, then "
                "analyse anything suspicious.\n\n"
            )
        return intro + (
            "Read the code at each location, confirm real issues, and record them "
            "with record_finding. Skip anything you cannot confirm."
        )


def _gather_shortlist(context: dict[str, Any]) -> str:
    """Pull the code reader's text output out of the upstream context."""
    parts = []
    for result in context.values():
        summary = result.get("summary") if isinstance(result, dict) else None
        if summary:
            parts.append(str(summary))
    return "\n\n".join(parts)
