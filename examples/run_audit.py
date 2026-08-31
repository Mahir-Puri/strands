"""Run a full audit against the bundled sample repo.

Usage:
    export ANTHROPIC_API_KEY=sk-ant-...
    python examples/run_audit.py

With no key set it will fail on the first live call, which is expected. The
GitHub step stays in dry-run mode unless you explicitly enable writes, so
this never files a real issue by accident.
"""

from __future__ import annotations

import os
from pathlib import Path

from strands import run_goal

HERE = Path(__file__).resolve().parent
SAMPLE = HERE / "sample_repo"


def main() -> None:
    goal = (
        "Audit this repository for security vulnerabilities and open a GitHub "
        "issue for each confirmed finding."
    )
    report = run_goal(
        goal=goal,
        repo_root=str(SAMPLE),
        github_repo=os.getenv("STRANDS_DEMO_REPO", "your-name/strands-demo"),
    )

    print(f"\nrun {report.run_id}  completed={report.completed}")
    print(f"plan: {len(report.plan.tasks)} tasks")
    for task in report.plan.tasks:
        print(f"  [{task.status.value:8}] {task.agent.value:16} {task.description[:60]}")

    print(f"\nfindings: {len(report.findings)}")
    for f in report.findings:
        loc = f"{f.file}:{f.line}" if f.line else f.file
        print(f"  [{f.severity.value:8}] {f.title}  ({loc})")

    print(f"\naudit records: {len(report.audit)}")


if __name__ == "__main__":
    main()
