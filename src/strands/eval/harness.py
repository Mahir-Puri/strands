"""The harness: run the suite, score each case, print a table.

The runner is injectable. In a live evaluation you pass the real run_goal and
it spends API calls. In tests you pass a stub that returns a canned report,
so the harness plumbing and the aggregate maths are exercised with no model.
The runner boundary is the whole trick for keeping this testable.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from ..schemas import RunReport
from .cases import BenchmarkCase, load_suite
from .metrics import Scores, match, operational, score

# A runner takes (goal, repo_root) and returns a RunReport.
Runner = Callable[[str, str], RunReport]

_DEFAULT_GOAL = "Audit this repository for security vulnerabilities and record every finding."


@dataclass
class CaseResult:
    name: str
    scores: Scores
    operational: dict[str, int]


def run_case(case: BenchmarkCase, runner: Runner, goal: str = _DEFAULT_GOAL) -> CaseResult:
    report = runner(goal, case.repo_root)
    match_result = match(report.findings, case.expected)
    return CaseResult(
        name=case.name,
        scores=score(match_result),
        operational=operational(report),
    )


def run_suite(benchmark_dir: str, runner: Runner, goal: str = _DEFAULT_GOAL) -> list[CaseResult]:
    return [run_case(case, runner, goal) for case in load_suite(benchmark_dir)]


def aggregate(results: list[CaseResult]) -> Scores:
    """Micro-averaged scores across the suite (pool all tp/fp/fn, then divide)."""
    tp = sum(r.scores.tp for r in results)
    fp = sum(r.scores.fp for r in results)
    fn = sum(r.scores.fn for r in results)
    precision = tp / (tp + fp) if (tp + fp) else 0.0
    recall = tp / (tp + fn) if (tp + fn) else 0.0
    f1 = (2 * precision * recall / (precision + recall)) if (precision + recall) else 0.0
    return Scores(precision=precision, recall=recall, f1=f1, tp=tp, fp=fp, fn=fn)


def render_table(results: list[CaseResult]) -> str:
    lines = []
    header = f"{'case':<22} {'prec':>6} {'recall':>7} {'f1':>6} {'tp':>3} {'fp':>3} {'fn':>3} {'llm':>4} {'tools':>6}"
    lines.append(header)
    lines.append("-" * len(header))
    for r in results:
        s = r.scores
        o = r.operational
        lines.append(
            f"{r.name[:22]:<22} {s.precision:>6.2f} {s.recall:>7.2f} {s.f1:>6.2f} "
            f"{s.tp:>3} {s.fp:>3} {s.fn:>3} {o['llm_calls']:>4} {o['tool_calls']:>6}"
        )
    agg = aggregate(results)
    lines.append("-" * len(header))
    lines.append(
        f"{'ALL (micro-avg)':<22} {agg.precision:>6.2f} {agg.recall:>7.2f} {agg.f1:>6.2f} "
        f"{agg.tp:>3} {agg.fp:>3} {agg.fn:>3}"
    )
    return "\n".join(lines)
