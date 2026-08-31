"""Offline-testable evaluation: cases, pure metrics, and a runner harness."""

from .cases import BenchmarkCase, load_case, load_suite
from .harness import CaseResult, aggregate, render_table, run_case, run_suite
from .metrics import ExpectedVuln, MatchResult, Scores, match, operational, score

__all__ = [
    "BenchmarkCase",
    "load_case",
    "load_suite",
    "CaseResult",
    "run_case",
    "run_suite",
    "aggregate",
    "render_table",
    "ExpectedVuln",
    "MatchResult",
    "Scores",
    "match",
    "score",
    "operational",
]
