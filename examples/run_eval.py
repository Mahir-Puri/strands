"""Run the evaluation suite and print a scored table.

    export ANTHROPIC_API_KEY=sk-ant-...
    python examples/run_eval.py

This spends API calls, one full pipeline run per case. The metrics themselves
are pure and tested offline; this script is just the live wiring that feeds
real runs into them.
"""

from __future__ import annotations

from pathlib import Path

from strands import run_goal
from strands.eval import render_table, run_suite

BENCHMARK = str(Path(__file__).resolve().parent.parent / "benchmark")


def runner(goal: str, repo_root: str):
    # github_repo is irrelevant here: the eval goal never asks to file issues,
    # so no writer task is planned.
    return run_goal(goal=goal, repo_root=repo_root)


def main() -> None:
    results = run_suite(BENCHMARK, runner)
    print()
    print(render_table(results))
    print()


if __name__ == "__main__":
    main()
