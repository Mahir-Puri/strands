"""Metrics. Deliberately pure functions.

Nothing here runs an agent or touches a model. It takes findings and a
ground-truth list and computes numbers. That separation is on purpose: the
part that decides whether the system is any good has no dependency on the
part that costs money to run, so it is fully unit-tested offline. When an
interviewer asks "how do you know your precision number is right", the answer
is "there is a test that pins it on a fixed input".

Matching rule, stated plainly so the numbers are interpretable: a recorded
finding matches an expected vulnerability when they are in the same file and
either their CWE ids match or the expected category word appears in the
finding's title or detail. Matching is greedy and one-to-one, so one finding
cannot satisfy two expected vulns and vice versa.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ..schemas import Finding


@dataclass
class ExpectedVuln:
    file: str
    category: str  # a short keyword, e.g. "sql_injection", "command_injection"
    cwe: str | None = None
    line: int | None = None


@dataclass
class MatchResult:
    true_positives: list[tuple[Finding, ExpectedVuln]] = field(default_factory=list)
    false_positives: list[Finding] = field(default_factory=list)
    false_negatives: list[ExpectedVuln] = field(default_factory=list)


def _matches(finding: Finding, expected: ExpectedVuln) -> bool:
    if _norm_path(finding.file) != _norm_path(expected.file):
        return False
    if finding.cwe and expected.cwe and finding.cwe.upper() == expected.cwe.upper():
        return True
    haystack = f"{finding.title} {finding.detail}".lower()
    needle = expected.category.replace("_", " ").lower()
    return needle in haystack


def _norm_path(path: str) -> str:
    # Compare on basename so "sample_repo/db.py" and "db.py" line up.
    return path.replace("\\", "/").rsplit("/", 1)[-1]


def match(findings: list[Finding], expected: list[ExpectedVuln]) -> MatchResult:
    result = MatchResult()
    remaining = list(findings)

    for exp in expected:
        hit = None
        for finding in remaining:
            if _matches(finding, exp):
                hit = finding
                break
        if hit is not None:
            result.true_positives.append((hit, exp))
            remaining.remove(hit)
        else:
            result.false_negatives.append(exp)

    result.false_positives = remaining
    return result


@dataclass
class Scores:
    precision: float
    recall: float
    f1: float
    tp: int
    fp: int
    fn: int


def score(result: MatchResult) -> Scores:
    tp = len(result.true_positives)
    fp = len(result.false_positives)
    fn = len(result.false_negatives)
    precision = tp / (tp + fp) if (tp + fp) else 0.0
    recall = tp / (tp + fn) if (tp + fn) else 0.0
    f1 = (2 * precision * recall / (precision + recall)) if (precision + recall) else 0.0
    return Scores(precision=precision, recall=recall, f1=f1, tp=tp, fp=fp, fn=fn)


def operational(report) -> dict[str, int]:
    """Counts pulled straight from the audit trail.

    This is the payoff for having logged everything: efficiency metrics come
    for free. How many model calls did the run cost, how many tool calls, how
    many retries, how many times did the supervisor step in.
    """
    kinds = [r.kind for r in report.audit]
    return {
        "llm_calls": kinds.count("llm_call") + kinds.count("llm_replay"),
        "tool_calls": kinds.count("tool_call"),
        "task_retries": kinds.count("task_retry"),
        "replan_reviews": kinds.count("replan_review"),
        "replans_applied": kinds.count("replan_applied"),
        "tasks_added": kinds.count("task_added"),
        "tasks_cancelled": kinds.count("task_cancelled"),
    }
