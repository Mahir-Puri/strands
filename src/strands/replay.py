"""Record and replay.

Every non-deterministic thing this system does comes from one place: the
model. The planner, the agents, and the supervisor all reach the model
through a single method, LLMClient.complete. So if you capture the ordered
list of responses that method produced during a live run, you can replay the
whole run later by feeding those same responses back, no API key and no
spend, and get an identical result.

That is what a cassette is here: an ordered log of model responses. Record
mode wraps the real client and appends each response to the cassette. Replay
mode swaps in a client that pops responses from the cassette in order. The
orchestrator, the agents, the tools, all of it runs unchanged. The tools do
re-execute for real (reading the repo, recording findings), which is fine
because they are deterministic given the same repo. The model was the only
moving part, and now it is nailed down.

This is event sourcing pointed at an agent: the run becomes a replayable
sequence instead of a thing that happened once and can never be inspected
again. It makes a run debuggable after the fact and makes regression tests
possible without a live model.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, Field

from .config import Settings
from .llm import LLMClient
from .memory import Memory
from .schemas import RunReport


class SerializedBlock(BaseModel):
    type: str
    text: str = ""
    name: str = ""
    id: str = ""
    input: dict[str, Any] = Field(default_factory=dict)


class SerializedResponse(BaseModel):
    blocks: list[SerializedBlock]
    stop_reason: str = "end_turn"


class Cassette(BaseModel):
    """An ordered recording of every model response in a run."""

    goal: str
    repo_root: str
    github_repo: str | None = None
    responses: list[SerializedResponse] = Field(default_factory=list)

    def save(self, path: str | Path) -> None:
        Path(path).write_text(self.model_dump_json(indent=2))

    @classmethod
    def load(cls, path: str | Path) -> Cassette:
        return cls.model_validate_json(Path(path).read_text())


# Small attribute-holding stand-ins so a rehydrated response quacks exactly
# like a real Anthropic one as far as the agent loop is concerned.
@dataclass
class _Block:
    type: str
    text: str = ""
    name: str = ""
    id: str = ""
    input: dict[str, Any] = field(default_factory=dict)


@dataclass
class _Response:
    content: list[_Block]
    stop_reason: str


def serialize_response(response: Any) -> SerializedResponse:
    blocks: list[SerializedBlock] = []
    for b in response.content:
        btype = getattr(b, "type", "text")
        if btype == "tool_use":
            blocks.append(
                SerializedBlock(
                    type="tool_use",
                    name=getattr(b, "name", ""),
                    id=getattr(b, "id", ""),
                    input=dict(getattr(b, "input", {}) or {}),
                )
            )
        else:
            blocks.append(SerializedBlock(type="text", text=getattr(b, "text", "")))
    return SerializedResponse(blocks=blocks, stop_reason=getattr(response, "stop_reason", "end_turn"))


def rehydrate(sr: SerializedResponse) -> _Response:
    blocks = [
        _Block(type=b.type, text=b.text, name=b.name, id=b.id, input=dict(b.input))
        for b in sr.blocks
    ]
    return _Response(content=blocks, stop_reason=sr.stop_reason)


class RecordingLLM(LLMClient):
    """A real client that also tucks every response into a cassette."""

    def __init__(self, settings: Settings, memory: Memory, cassette: Cassette):
        super().__init__(settings, memory)
        self.cassette = cassette

    def complete(self, **kwargs: Any) -> Any:
        response = super().complete(**kwargs)
        self.cassette.responses.append(serialize_response(response))
        return response


class ReplayExhausted(BaseException):
    """The cassette ran out of responses before the run finished.

    If this fires, the replayed run diverged from the recorded one, which
    almost always means the code changed since the recording. That is a
    genuinely useful signal, not just an error.

    It subclasses BaseException on purpose, not Exception. A divergence is not
    an operational hiccup to retry past like a timeout; it means the replay is
    invalid, so it should punch straight through the task retry loop and stop
    the run, the same way KeyboardInterrupt does.
    """


class ReplayLLM(LLMClient):
    """Returns recorded responses in order instead of calling the model."""

    def __init__(self, settings: Settings, memory: Memory, cassette: Cassette):
        super().__init__(settings, memory)
        self._responses = list(cassette.responses)
        self._i = 0

    def complete(self, *, actor: str = "llm", **kwargs: Any) -> Any:
        if self._i >= len(self._responses):
            raise ReplayExhausted(
                f"cassette exhausted after {self._i} responses; the run diverged from the recording"
            )
        sr = self._responses[self._i]
        self._i += 1
        self.memory.audit.record(
            kind="llm_replay",
            actor=actor,
            summary=f"replayed response {self._i}/{len(self._responses)}",
        )
        return rehydrate(sr)


def record_run(
    goal: str,
    repo_root: str,
    github_repo: str | None,
    settings: Settings,
) -> tuple[RunReport, Cassette]:
    """Run a goal live while capturing a cassette of every model response."""
    from .orchestrator import build_orchestrator

    memory = Memory()
    cassette = Cassette(goal=goal, repo_root=repo_root, github_repo=github_repo)
    llm = RecordingLLM(settings, memory, cassette)
    run_id = uuid4().hex[:12]
    memory.audit.record(kind="run_start", actor="strands", summary=f"record {run_id}", run_id=run_id)
    orch = build_orchestrator(settings, memory, repo_root, github_repo, llm=llm)
    report = orch.run(goal, run_id)
    return report, cassette


def replay_run(cassette: Cassette, settings: Settings) -> RunReport:
    """Re-run a recorded cassette with no live model calls."""
    from .orchestrator import build_orchestrator

    memory = Memory()
    llm = ReplayLLM(settings, memory, cassette)
    run_id = uuid4().hex[:12]
    memory.audit.record(kind="run_start", actor="strands", summary=f"replay {run_id}", run_id=run_id)
    orch = build_orchestrator(settings, memory, cassette.repo_root, cassette.github_repo, llm=llm)
    return orch.run(cassette.goal, run_id)


def reports_match(a: RunReport, b: RunReport) -> tuple[bool, list[str]]:
    """Compare two runs on the things that should be identical.

    Ids and timestamps are allowed to differ (they are generated fresh each
    run). What must match is the substance: task statuses in order, and the
    set of findings by location, severity, and title.
    """
    diffs: list[str] = []

    a_tasks = [(t.agent.value, t.status.value) for t in a.plan.tasks]
    b_tasks = [(t.agent.value, t.status.value) for t in b.plan.tasks]
    if a_tasks != b_tasks:
        diffs.append(f"task statuses differ: {a_tasks} vs {b_tasks}")

    def key(report: RunReport) -> set[tuple]:
        return {(f.file, f.line, f.severity.value, f.title) for f in report.findings}

    if key(a) != key(b):
        diffs.append(f"findings differ: {sorted(key(a))} vs {sorted(key(b))}")

    return (not diffs, diffs)
