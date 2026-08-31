"""HTTP surface.

A small FastAPI app so the system can be driven over the network instead of
only from a script. Runs are executed in a background thread and their state
is polled. This is intentionally in-memory and single-node: it is a demo and
portfolio surface, not a distributed job queue. The honest version of that
statement is in the README under Limitations.
"""

from __future__ import annotations

import threading
from typing import Any
from uuid import uuid4

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from .config import load_settings
from .memory import Memory
from .orchestrator import build_orchestrator
from .schemas import RunReport

app = FastAPI(title="Strands", version="0.1.0")

# run_id -> state. Fine for a single process; see Limitations in the README.
_RUNS: dict[str, dict[str, Any]] = {}
_LOCK = threading.Lock()


class RunRequest(BaseModel):
    goal: str
    repo_root: str
    github_repo: str | None = None


class RunAck(BaseModel):
    run_id: str
    status: str


@app.post("/runs", response_model=RunAck)
def create_run(req: RunRequest) -> RunAck:
    run_id = uuid4().hex[:12]
    with _LOCK:
        _RUNS[run_id] = {"status": "running", "report": None, "error": None}

    def _work() -> None:
        try:
            cfg = load_settings()
            memory = Memory()
            memory.audit.record(
                kind="run_start", actor="strands", summary=f"run {run_id}", run_id=run_id
            )
            orch = build_orchestrator(cfg, memory, req.repo_root, req.github_repo)
            report = orch.run(req.goal, run_id)
            with _LOCK:
                _RUNS[run_id] = {"status": "done", "report": report, "error": None}
        except Exception as exc:  # surface the failure to the poller
            with _LOCK:
                _RUNS[run_id] = {"status": "error", "report": None, "error": str(exc)}

    threading.Thread(target=_work, daemon=True).start()
    return RunAck(run_id=run_id, status="running")


@app.get("/runs/{run_id}")
def get_run(run_id: str) -> dict[str, Any]:
    with _LOCK:
        state = _RUNS.get(run_id)
    if state is None:
        raise HTTPException(status_code=404, detail="unknown run id")
    report: RunReport | None = state["report"]
    return {
        "run_id": run_id,
        "status": state["status"],
        "error": state["error"],
        "findings": [f.model_dump() for f in report.findings] if report else [],
        "audit_len": len(report.audit) if report else 0,
        "completed": report.completed if report else False,
    }


@app.get("/runs/{run_id}/audit")
def get_audit(run_id: str) -> dict[str, Any]:
    with _LOCK:
        state = _RUNS.get(run_id)
    if state is None or state["report"] is None:
        raise HTTPException(status_code=404, detail="no audit for that run id yet")
    report: RunReport = state["report"]
    return {"run_id": run_id, "audit": [r.model_dump() for r in report.audit]}


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}
