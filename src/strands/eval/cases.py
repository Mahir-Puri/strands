"""Loading benchmark cases from disk.

A case is a directory with source files to audit plus an expected.json that
lists the vulnerabilities that are actually in it. The harness runs the
pipeline against the code and scores what it found against that list.

Building the ground truth by hand is the honest part of this. The numbers
only mean something because a human decided what the right answer was, not
because the model graded its own homework.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from .metrics import ExpectedVuln


@dataclass
class BenchmarkCase:
    name: str
    repo_root: str
    expected: list[ExpectedVuln]


def load_case(case_dir: str | Path) -> BenchmarkCase:
    case_path = Path(case_dir)
    manifest = case_path / "expected.json"
    if not manifest.is_file():
        raise FileNotFoundError(f"no expected.json in {case_path}")
    data = json.loads(manifest.read_text())
    expected = [
        ExpectedVuln(
            file=item["file"],
            category=item["category"],
            cwe=item.get("cwe"),
            line=item.get("line"),
        )
        for item in data.get("vulnerabilities", [])
    ]
    # The code to audit lives in a "repo" subdir so the manifest itself is not
    # part of what gets scanned.
    repo_root = case_path / data.get("repo_dir", "repo")
    return BenchmarkCase(name=data.get("name", case_path.name), repo_root=str(repo_root), expected=expected)


def load_suite(benchmark_dir: str | Path) -> list[BenchmarkCase]:
    base = Path(benchmark_dir)
    cases = []
    for child in sorted(base.iterdir()):
        if child.is_dir() and (child / "expected.json").is_file():
            cases.append(load_case(child))
    return cases
