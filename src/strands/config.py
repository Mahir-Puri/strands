"""Runtime configuration.

Read once from the environment at import time. Nothing here reaches out to
a network or a disk, so it is safe to import from anywhere including tests.
"""

from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    anthropic_api_key: str
    model: str
    planner_model: str
    max_task_attempts: int
    max_agent_steps: int
    replan_enabled: bool
    max_replans: int
    github_token: str
    github_write_enabled: bool
    request_timeout_s: float

    @property
    def has_anthropic(self) -> bool:
        return bool(self.anthropic_api_key)

    @property
    def has_github(self) -> bool:
        return bool(self.github_token)


def load_settings() -> Settings:
    """Build a Settings object from the current environment.

    Defaults are chosen so the thing boots without a full setup. The two
    write paths (talking to Anthropic, creating GitHub issues) both stay
    off unless you supply the relevant credential, which is the point.
    """

    return Settings(
        anthropic_api_key=os.getenv("ANTHROPIC_API_KEY", ""),
        model=os.getenv("STRANDS_MODEL", "claude-sonnet-4-6"),
        planner_model=os.getenv("STRANDS_PLANNER_MODEL", os.getenv("STRANDS_MODEL", "claude-sonnet-4-6")),
        max_task_attempts=int(os.getenv("STRANDS_MAX_TASK_ATTEMPTS", "3")),
        max_agent_steps=int(os.getenv("STRANDS_MAX_AGENT_STEPS", "12")),
        # The supervisor reviews the plan after each wave and can revise it.
        # Bounded so a run cannot replan forever.
        replan_enabled=os.getenv("STRANDS_REPLAN", "true").lower() == "true",
        max_replans=int(os.getenv("STRANDS_MAX_REPLANS", "3")),
        github_token=os.getenv("GITHUB_TOKEN", ""),
        # Writing real issues is opt-in. Default is a dry run that logs what
        # it would have created. You have to mean it to flip this on.
        github_write_enabled=os.getenv("STRANDS_GITHUB_WRITE", "false").lower() == "true",
        request_timeout_s=float(os.getenv("STRANDS_TIMEOUT_S", "60")),
    )


settings = load_settings()
