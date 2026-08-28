"""GitHub issue creation, gated behind a dry-run wall.

This is the only tool that changes something outside the process, so it is
the one I was most careful with. Two rules:

1. It never creates a real issue unless STRANDS_GITHUB_WRITE is true AND a
   token is present. Default behaviour is a dry run that logs the exact
   payload it would have sent. You have to opt in to touch the outside world.

2. The agent proposes, it does not decide unilaterally. Same rule I used on
   an earlier project where the AI copilot could read the ledger but was
   never allowed near the settlement path. An LLM that files real issues on
   its own is a great way to spam a repo.
"""

from __future__ import annotations

import json
import urllib.request
from typing import Any

from ..config import Settings
from ..memory import Memory
from .base import ToolRegistry


def register_github_tools(
    registry: ToolRegistry,
    memory: Memory,
    settings: Settings,
    repo: str,
) -> None:
    """repo is 'owner/name'."""

    @registry.add(
        name="open_issue",
        description=(
            "Open a GitHub issue for a finding. In dry-run mode (the default) this "
            "does not create anything, it records what it would create. Provide a "
            "clear title and a body written for a maintainer."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "title": {"type": "string"},
                "body": {"type": "string"},
                "labels": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Optional labels, e.g. ['security', 'high'].",
                },
            },
            "required": ["title", "body"],
        },
    )
    def open_issue(title: str, body: str, labels: list[str] | None = None) -> dict[str, Any]:
        payload = {"title": title, "body": body, "labels": labels or []}
        live = settings.github_write_enabled and settings.has_github

        if not live:
            memory.audit.record(
                kind="github_dry_run",
                actor="github_writer",
                summary=f"would open issue: {title}",
                repo=repo,
                payload=payload,
            )
            return {
                "created": False,
                "dry_run": True,
                "reason": "github writes are disabled (set STRANDS_GITHUB_WRITE=true and GITHUB_TOKEN to enable)",
                "would_send": payload,
            }

        url = f"https://api.github.com/repos/{repo}/issues"
        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(url, data=data, method="POST")
        req.add_header("Authorization", f"Bearer {settings.github_token}")
        req.add_header("Accept", "application/vnd.github+json")
        req.add_header("Content-Type", "application/json")
        with urllib.request.urlopen(req, timeout=settings.request_timeout_s) as resp:
            created = json.loads(resp.read().decode("utf-8"))
        memory.audit.record(
            kind="github_issue",
            actor="github_writer",
            summary=f"opened issue #{created.get('number')}: {title}",
            repo=repo,
            number=created.get("number"),
            url=created.get("html_url"),
        )
        return {"created": True, "number": created.get("number"), "url": created.get("html_url")}
