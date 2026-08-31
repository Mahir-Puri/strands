"""Record a live run, then replay it offline and prove they match.

    export ANTHROPIC_API_KEY=sk-ant-...
    python examples/record_and_replay.py

The record step spends API calls. The replay step spends nothing and needs no
key: it re-runs the exact same pipeline from the cassette. If the two reports
match, you have a run you can re-examine forever without touching the model.
"""

from __future__ import annotations

from pathlib import Path

from strands.config import load_settings
from strands.replay import Cassette, record_run, replay_run, reports_match

SAMPLE = str(Path(__file__).resolve().parent / "sample_repo")
CASSETTE_PATH = Path(__file__).resolve().parent / "run.cassette.json"


def main() -> None:
    settings = load_settings()
    goal = "Audit this repository for security vulnerabilities and record every finding."

    print("recording a live run...")
    live_report, cassette = record_run(goal, SAMPLE, None, settings)
    cassette.save(CASSETTE_PATH)
    print(f"  captured {len(cassette.responses)} model responses -> {CASSETTE_PATH.name}")
    print(f"  live run found {len(live_report.findings)} findings")

    print("replaying from the cassette (no model calls)...")
    loaded = Cassette.load(CASSETTE_PATH)
    replayed = replay_run(loaded, settings)
    print(f"  replay found {len(replayed.findings)} findings")

    ok, diffs = reports_match(live_report, replayed)
    if ok:
        print("\nmatch: the replayed run is identical to the recorded one.")
    else:
        print("\nMISMATCH:")
        for d in diffs:
            print(f"  - {d}")


if __name__ == "__main__":
    main()
