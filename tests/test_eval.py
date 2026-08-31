"""Eval tests.

The metrics are pure, so these pin exact numbers on fixed inputs. That is the
whole point of keeping them dependency-free: the thing that judges quality is
itself checkable without a model in the loop.
"""

from __future__ import annotations

from pathlib import Path

from strands.eval import (
    ExpectedVuln,
    aggregate,
    load_suite,
    match,
    operational,
    render_table,
    run_suite,
    score,
)
from strands.schemas import AuditRecord, Finding, Plan, RunReport, Severity, Task


def _finding(file, title, detail="", cwe=None):
    return Finding(file=file, severity=Severity.HIGH, title=title, detail=detail, cwe=cwe)


def test_match_on_cwe():
    findings = [_finding("app.py", "SQLi", cwe="CWE-89")]
    expected = [ExpectedVuln(file="app.py", category="sql_injection", cwe="CWE-89")]
    result = match(findings, expected)
    assert len(result.true_positives) == 1
    assert not result.false_positives
    assert not result.false_negatives


def test_match_on_category_keyword_when_no_cwe():
    findings = [_finding("app.py", "Command injection in ping handler")]
    expected = [ExpectedVuln(file="app.py", category="command_injection")]
    result = match(findings, expected)
    assert len(result.true_positives) == 1


def test_basename_path_matching():
    findings = [_finding("repo/sub/db.py", "SQLi", cwe="CWE-89")]
    expected = [ExpectedVuln(file="db.py", category="sql_injection", cwe="CWE-89")]
    assert len(match(findings, expected).true_positives) == 1


def test_false_positive_and_negative_counted():
    findings = [_finding("app.py", "made up issue", cwe="CWE-000")]
    expected = [ExpectedVuln(file="app.py", category="sql_injection", cwe="CWE-89")]
    result = match(findings, expected)
    assert len(result.false_positives) == 1
    assert len(result.false_negatives) == 1
    s = score(result)
    assert s.precision == 0.0
    assert s.recall == 0.0


def test_clean_repo_perfect_when_nothing_found():
    result = match([], [])
    s = score(result)
    # no findings and none expected is a clean pass; f1 defined as 0 with no signal
    assert s.tp == 0 and s.fp == 0 and s.fn == 0


def test_clean_repo_penalises_false_positive():
    findings = [_finding("auth.py", "phantom issue")]
    result = match(findings, [])
    s = score(result)
    assert s.fp == 1
    assert s.precision == 0.0


def test_one_finding_cannot_satisfy_two_expected():
    findings = [_finding("app.py", "SQLi", cwe="CWE-89")]
    expected = [
        ExpectedVuln(file="app.py", category="sql_injection", cwe="CWE-89"),
        ExpectedVuln(file="app.py", category="sql_injection", cwe="CWE-89"),
    ]
    result = match(findings, expected)
    assert len(result.true_positives) == 1
    assert len(result.false_negatives) == 1


def test_perfect_score_numbers():
    findings = [
        _finding("app.py", "SQLi", cwe="CWE-89"),
        _finding("app.py", "cmd", cwe="CWE-78"),
    ]
    expected = [
        ExpectedVuln(file="app.py", category="sql_injection", cwe="CWE-89"),
        ExpectedVuln(file="app.py", category="command_injection", cwe="CWE-78"),
    ]
    s = score(match(findings, expected))
    assert s.precision == 1.0 and s.recall == 1.0 and s.f1 == 1.0


def _report_with(findings, kinds):
    audit = [AuditRecord(kind=k, actor="x", summary="") for k in kinds]
    plan = Plan(goal="g", tasks=[Task(agent=__import__("strands.schemas", fromlist=["AgentType"]).AgentType.CODE_READER, description="d")])
    return RunReport(run_id="r", goal="g", plan=plan, findings=findings, audit=audit)


def test_operational_counts_from_audit():
    report = _report_with([], ["llm_call", "llm_call", "tool_call", "task_retry", "replan_applied"])
    ops = operational(report)
    assert ops["llm_calls"] == 2
    assert ops["tool_calls"] == 1
    assert ops["task_retries"] == 1
    assert ops["replans_applied"] == 1


def test_aggregate_micro_average():
    from strands.eval.harness import CaseResult

    a = CaseResult(name="a", scores=score(match([_finding("f.py", "x", cwe="CWE-1")], [ExpectedVuln("f.py", "x", "CWE-1")])), operational={})
    b = CaseResult(name="b", scores=score(match([], [ExpectedVuln("g.py", "y", "CWE-2")])), operational={})
    agg = aggregate([a, b])
    # one tp overall, one fn overall -> recall 0.5, precision 1.0
    assert agg.tp == 1 and agg.fn == 1
    assert agg.precision == 1.0
    assert agg.recall == 0.5


def test_harness_runs_with_a_stub_runner(tmp_path: Path):
    # A stub runner returns a canned report, so the harness plumbing is tested
    # without a model. This mirrors how run_eval.py wires in the real thing.
    def stub_runner(goal: str, repo_root: str) -> RunReport:
        return _report_with([_finding("app.py", "SQLi", cwe="CWE-89")], ["llm_call"])

    # build a one-case suite on disk
    case = tmp_path / "case01"
    (case / "repo").mkdir(parents=True)
    (case / "repo" / "app.py").write_text("x = 1\n")
    (case / "expected.json").write_text(
        '{"name": "c1", "repo_dir": "repo", "vulnerabilities": '
        '[{"file": "app.py", "category": "sql_injection", "cwe": "CWE-89"}]}'
    )

    results = run_suite(str(tmp_path), stub_runner)
    assert len(results) == 1
    assert results[0].scores.f1 == 1.0
    table = render_table(results)
    assert "c1" in table


def test_load_suite_reads_bundled_benchmark():
    benchmark = Path(__file__).resolve().parent.parent / "benchmark"
    cases = load_suite(benchmark)
    names = {c.name for c in cases}
    assert {"web_app", "command_exec", "clean"}.issubset(names)
    clean = next(c for c in cases if c.name == "clean")
    assert clean.expected == []
