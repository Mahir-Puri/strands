"""Replay tests.

Rather than record a live run (which needs a key), these hand-build a
cassette of model responses and replay it. That exercises the real seam:
ReplayLLM feeding rehydrated responses into the actual orchestrator, agents,
and tools, and reproducing findings and task statuses deterministically.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from strands.replay import (
    Cassette,
    ReplayExhausted,
    SerializedBlock,
    SerializedResponse,
    replay_run,
    reports_match,
)
from strands.schemas import Severity


def _text(t: str) -> SerializedResponse:
    return SerializedResponse(blocks=[SerializedBlock(type="text", text=t)], stop_reason="end_turn")


def _tool(name: str, tool_input: dict) -> SerializedResponse:
    return SerializedResponse(
        blocks=[SerializedBlock(type="tool_use", name=name, id="tu1", input=tool_input)],
        stop_reason="tool_use",
    )


def _plan_json() -> str:
    return (
        '{"tasks": ['
        '{"id": "t1", "agent": "code_reader", "description": "read", "depends_on": []},'
        '{"id": "t2", "agent": "vuln_classifier", "description": "classify", "depends_on": ["t1"]}'
        "]}"
    )


def _build_cassette(repo_root: str) -> Cassette:
    """A scripted run: planner makes 2 tasks, reader replies, classifier
    records one finding then summarises. Replan disabled for a tight script."""
    return Cassette(
        goal="audit it",
        repo_root=repo_root,
        responses=[
            _text(_plan_json()),                     # planner
            _text("shortlist: app.py has a raw SQL string"),  # code_reader
            _tool("record_finding", {                # classifier records
                "file": "app.py",
                "line": 1,
                "severity": "high",
                "title": "SQL injection",
                "detail": "string built query",
                "cwe": "CWE-89",
                "confidence": 0.9,
            }),
            _text("recorded one SQL injection finding"),  # classifier summary
        ],
    )


@pytest.fixture
def no_replan(settings):
    from dataclasses import replace

    return replace(settings, replan_enabled=False)


def test_replay_reproduces_findings(no_replan, sample_repo: Path):
    cassette = _build_cassette(str(sample_repo))
    report = replay_run(cassette, no_replan)
    assert len(report.findings) == 1
    assert report.findings[0].severity is Severity.HIGH
    assert report.findings[0].cwe == "CWE-89"


def test_two_replays_are_identical(no_replan, sample_repo: Path):
    cassette = _build_cassette(str(sample_repo))
    first = replay_run(cassette, no_replan)
    second = replay_run(cassette, no_replan)
    ok, diffs = reports_match(first, second)
    assert ok, diffs


def test_cassette_round_trips_through_disk(no_replan, sample_repo: Path, tmp_path: Path):
    cassette = _build_cassette(str(sample_repo))
    path = tmp_path / "run.json"
    cassette.save(path)
    loaded = Cassette.load(path)
    assert loaded.goal == cassette.goal
    assert len(loaded.responses) == len(cassette.responses)
    report = replay_run(loaded, no_replan)
    assert len(report.findings) == 1


def test_exhausted_cassette_raises(no_replan, sample_repo: Path):
    # Only a planner response, nothing for the agents. Replay must not hang or
    # silently pass; it should flag that the run diverged from the recording.
    short = Cassette(goal="g", repo_root=str(sample_repo), responses=[_text(_plan_json())])
    with pytest.raises(ReplayExhausted):
        replay_run(short, no_replan)
