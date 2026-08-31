"""Strands: a multi-agent system that plans, uses tools, and audits itself.

Public entry point is run_goal. Everything else is wiring you can reach into
if you want to, but most callers just need this.
"""

from __future__ import annotations

from uuid import uuid4

from .config import Settings, load_settings, settings
from .memory import Memory
from .orchestrator import build_orchestrator
from .replay import Cassette, record_run, replay_run, reports_match
from .schemas import RunReport

__all__ = [
    "run_goal",
    "Settings",
    "load_settings",
    "settings",
    "RunReport",
    "Memory",
    "build_orchestrator",
    "Cassette",
    "record_run",
    "replay_run",
    "reports_match",
]

__version__ = "0.2.0"


def run_goal(
    goal: str,
    repo_root: str,
    github_repo: str | None = None,
    settings_override: Settings | None = None,
) -> RunReport:
    """Plan and execute a goal against a target repository.

    goal is plain language, e.g. "audit this repo for security issues and
    open a GitHub issue for each finding". repo_root is the path to the code
    to look at. github_repo is 'owner/name' and only matters if the plan
    includes filing issues.
    """

    cfg = settings_override or load_settings()
    memory = Memory()
    run_id = uuid4().hex[:12]
    memory.audit.record(
        kind="run_start",
        actor="strands",
        summary=f"run {run_id}: {goal[:100]}",
        run_id=run_id,
        repo_root=repo_root,
    )
    orchestrator = build_orchestrator(cfg, memory, repo_root, github_repo)
    return orchestrator.run(goal, run_id)
