# Strands

Strands is a multi-agent system that takes a plain-language goal, breaks it into a plan, runs that plan with a set of specialist agents that use real tools, and revises the plan as it learns. The example task it ships with is a security audit: point it at a repository, and it surveys the code, confirms actual vulnerabilities, and drafts a GitHub issue for each one.

I built it because most "agentic AI" demos I came across are a single prompt in a loop with a couple of tools bolted on. I wanted the version where planning, execution, tool use, memory, replanning, and failure handling are separate, inspectable pieces, so I could explain why each decision was made instead of hoping the framework did the right thing.

The thing I care about most is the loop: it plans, it acts, it observes what came back, and it changes the remaining plan when reality does not match what it expected. That plan-observe-revise cycle is the line between a workflow that follows a fixed script and an agent that adapts, and it is the part most student projects skip.

The name is because the whole thing is a bunch of separate strands of work that get braided together by a planner.

Three things come with it beyond the core loop: a **replanning supervisor** that revises the plan mid-run, an **evaluation harness** that scores the pipeline against repos with known planted bugs so quality is a number and not a vibe, and **deterministic replay** that re-runs any recorded run offline with no model calls.

## What it actually does

Give it a goal like:

> "Audit this repository for security vulnerabilities and open a GitHub issue for each confirmed finding."

Here is what happens:

1. The **planner** reads the goal and produces a task graph. It decides which agents run, in what order, and what depends on what. For the goal above it produces three tasks: survey the code, classify vulnerabilities, then write issues, with each depending on the one before.

2. The **orchestrator** walks that graph. It runs a task only once its dependencies are done, retries tasks that fail, and skips tasks whose upstream work failed instead of running them on half the data.

3. Three **sub-agents** do the work. The code reader surveys the repo and shortlists suspicious spots. The vulnerability classifier reads those spots, confirms the real issues, and records them as structured findings. The GitHub writer turns findings into issue drafts.

4. Between waves, the **supervisor** looks at what happened and decides whether the rest of the plan still makes sense. If the classifier found nothing, it cancels the pointless issue-writing task. If a reader surfaced something the plan did not anticipate, it adds a task to chase it. This is bounded, so a run cannot replan forever.

5. Everything the system does lands in an **audit trail**: every plan, task, tool call, retry, replan, and failure, in order. You can read a run back start to finish without guessing what the model was thinking, and you can replay it exactly.

## Why it is built this way

This is the part I care about most, so it goes near the top.

**Planning is separated from execution.** The planner does not run any tools. Its only job is decomposition. Pulling it out means I can test "did it produce a sensible plan" without spending a single API call on execution, and I can swap the planning strategy without touching the agents. Deciding what to do and doing it are genuinely different problems and mixing them into one giant prompt makes both worse.

**Each agent is a narrow specialist with scoped tools.** The code reader physically cannot open a GitHub issue, because that tool is not in its registry. The writer cannot read arbitrary files. This is least privilege enforced by wiring, not by writing "please do not do X" in a system prompt and hoping. It also keeps each agent's context small, which cuts token cost and cuts the hallucination you get when you stuff every tool and every file into one prompt.

**Findings are structured, not narrated.** The classifier does not get to write "I think there is a SQL injection somewhere" in prose. It has to call `record_finding` with a file, a severity, a CWE id, and a fix. If it cannot fill in the shape, it does not get to claim the finding. That structure is what makes the output auditable and countable rather than a wall of text.

**The one tool that changes the outside world is gated.** Filing a GitHub issue is the only action with real side effects, so it defaults to a dry run that logs exactly what it would have filed. You have to set two separate environment variables to let it write for real. I used the same rule on an earlier payments project: the AI could read everything but was never allowed near the part that actually moved money. An agent that files real issues on its own is a great way to spam a repo you care about.

**Failure is expected, not exceptional.** LLM calls and network calls fail in transient ways. Every task gets a bounded number of retries with backoff and jitter. When a task runs out of retries it is marked failed and the run continues, so one flaky step does not throw away the whole audit. Agents also have a hard step cap, so a confused agent stops instead of looping forever.

**Replanning goes through the same LLM boundary as everything else.** The supervisor is not a special case bolted onto the side. It reasons about the plan by calling the exact same `LLMClient.complete` that the planner and agents use. That one decision pays off twice: the replanning loop is testable with the same scripted-model trick as the rest, and replay works on replanned runs for free, because the supervisor's model calls land in the same cassette as everyone else's. When one clean seam does three jobs, that is usually a sign the abstraction is right.

## Architecture

```
                    goal (plain language)
                          |
                          v
                   +-------------+
                   |   Planner   |   decomposes goal into a task graph
                   +-------------+
                          |
                          v
                   +-------------+  <----------------------+
                   | Orchestrator|                         |
                   +-------------+                         |
                    /     |      \                         |
                   v      v       v                        |
          +-----------+ +-----------+ +--------------+     | revise plan:
          |CodeReader | |VulnClass. | |GitHubWriter  |     | add / cancel
          +-----------+ +-----------+ +--------------+     | tasks
             |  tools      |  tools       |  tools         |
             v             v              v                |
          list_files    read_file      open_issue         |
          read_file     record_finding (dry-run)          |
                          |                                |
                          v                                |
                   +-------------+   after each wave,      |
                   | Supervisor  |---observe + decide------+
                   +-------------+
                          |
                          v
                 +------------------+
                 |  Shared Memory   |   scoped scratchpad per agent
                 |  + Audit Trail   |   append-only, replayable
                 +------------------+
```

The loop back from the supervisor to the orchestrator is the whole point: plan, act, observe, revise, repeat, until the plan is done or the replan budget runs out.

Every arrow into memory is one-way and append-only. Agents write their own namespace in the scratchpad; the orchestrator is the only thing that hands one agent's output to another, so there is never a hidden path where two agents quietly feed each other in a loop.

## Layout

```
src/strands/
  schemas.py        data models: Plan, Task, Finding, PlanRevision, AuditRecord
  config.py         settings from environment variables
  retry.py          backoff-with-jitter retry helper
  memory.py         audit trail + scoped scratchpad + findings
  llm.py            Anthropic wrapper and the tool-use agent loop
  planner.py        goal -> task graph, with a fallback plan
  supervisor.py     observes a running plan and revises it (the agent loop)
  orchestrator.py   runs the graph: scheduling, retries, skips, replanning
  replay.py         record a run to a cassette, replay it with no model calls
  agents/           the three specialists
  tools/            filesystem, vulnerability, github tools
  eval/             pure metrics + case loading + scoring harness
  api.py            FastAPI surface
benchmark/          scored cases: two vulnerable repos and one clean one
examples/
  run_audit.py         audit the bundled sample repo
  run_eval.py          score the pipeline against the benchmark
  record_and_replay.py record a live run then replay it offline
  sample_repo/         a small app with deliberate holes to find
tests/              full offline suite against a scripted fake model
```

## Running it

You need Python 3.10 or newer.

```bash
pip install -e ".[dev]"
```

To run the example audit against the bundled sample repo:

```bash
export ANTHROPIC_API_KEY=sk-ant-...
python examples/run_audit.py
```

The sample repo under `examples/sample_repo` has a handful of planted problems (a string-built SQL query, a shell call on user input, a committed secret, MD5 password hashing). It exists so there is something real to find. The GitHub step stays in dry run unless you turn it on, so this will not file anything.

To run it as an HTTP service:

```bash
uvicorn strands.api:app --reload
# then
curl -X POST localhost:8000/runs \
  -H 'content-type: application/json' \
  -d '{"goal": "audit this repo and open issues", "repo_root": "./examples/sample_repo"}'
```

Poll `GET /runs/{run_id}` for status and findings, and `GET /runs/{run_id}/audit` for the full trail.

### Configuration

Copy `.env.example` to `.env`. The settings that matter:

- `ANTHROPIC_API_KEY` is required for any live run.
- `STRANDS_MODEL` picks the model. Set it to whatever you have access to.
- `STRANDS_GITHUB_WRITE` plus `GITHUB_TOKEN` are both required before it will file a real issue. Leave them off to stay in dry run.
- `STRANDS_MAX_TASK_ATTEMPTS` and `STRANDS_MAX_AGENT_STEPS` bound retries and the agent loop.
- `STRANDS_REPLAN` turns the supervisor on or off, and `STRANDS_MAX_REPLANS` caps how many times a single run may revise its plan. Turning replanning off is how you compare "workflow mode" against "agent mode" on the same benchmark.

## Evaluation

The point of the benchmark is to turn "trust me, it finds bugs" into a number. Each case under `benchmark/` is a repo plus an `expected.json` listing the vulnerabilities that are actually in it, written by hand. The harness runs the pipeline against each repo and scores what it found.

```bash
export ANTHROPIC_API_KEY=sk-ant-...
python examples/run_eval.py
```

You get a table with precision, recall, and F1 per case, plus operational numbers pulled straight from the audit trail: how many model calls each run cost, how many tool calls, how many times the supervisor stepped in. There are three cases on purpose: two vulnerable repos and one that is clean. The clean one exists to measure false positives, because a security tool that cries wolf on safe code is worse than useless.

The matching rule is stated plainly in `eval/metrics.py`: a finding matches an expected vuln when they are in the same file and either their CWE ids agree or the expected category word shows up in the finding. Matching is one-to-one so one finding cannot be counted twice. All of that logic is pure and unit-tested on fixed inputs, so the scoring itself never depends on a live model. I did not want a benchmark where the model grades its own homework.

I am not publishing a headline accuracy number here because it moves with the model and the prompt, and a number I cannot reproduce for you is worse than none. Run it against your own key and you will get the current figure.

## Replay

Every non-deterministic thing this system does comes from the model, and every model call goes through one method. So a run can be recorded to a cassette (the ordered list of model responses) and replayed later with no API key and no spend.

```bash
export ANTHROPIC_API_KEY=sk-ant-...
python examples/record_and_replay.py
```

That records a live run, saves the cassette, replays it offline, and checks the two runs match on the things that should be identical (task statuses and the set of findings). Ids and timestamps are allowed to differ; the substance is not.

This is event sourcing pointed at an agent. It makes a run debuggable after the fact, and it makes regression testing possible: capture a cassette once, and you can re-run that exact scenario in CI forever without touching the model. The tools re-execute for real during replay (reading the repo, recording findings), which is fine because they are deterministic given the same repo. The model was the only moving part, and the cassette pins it down. If the code changes enough that the run diverges, replay runs out of recorded responses and says so, which is a useful signal rather than a silent wrong answer.

## Tests

```bash
pytest
```

The whole suite runs offline, all fifty-odd tests. There is a scripted fake model in `tests/conftest.py` that returns canned responses and tool calls, so the planner, orchestrator, supervisor, replanning loop, retry logic, memory scoping, tool sandboxing, eval metrics, and replay all get exercised without an API key or a network. The only thing not covered is the real Anthropic transport itself, which is the right line to draw: I am testing my logic, not their SDK.

CI runs the suite on Python 3.10 through 3.12 plus a ruff lint pass, with no API key present, which is a nice forcing function for keeping the core logic testable offline.

## Limitations

I would rather be straight about these than oversell.

- **Runs are in memory.** The API keeps run state in a dict in one process. That is fine for a demo and a portfolio piece and wrong for anything that needs to survive a restart or scale past one node. A real version would put run state and the audit trail in a database and the task queue in something durable.
- **The security analysis is only as good as the model.** This is a reasoning-driven audit, not a replacement for a real static analysis tool. It will miss things a proper analyzer catches and it can be wrong. The structured findings, the confidence scores, and the benchmark are there so you can check its work, not so you can trust it blind.
- **The task graph runs sequentially.** Independent tasks could run in parallel. The scheduling already knows what is independent, so this is wiring rather than redesign, but I have not done it yet. It would also mean the replay cassette needs keys instead of a plain order, since concurrent model calls would not have a fixed sequence.
- **Replay pins the model, not the repo.** The cassette records model responses, not tool results, so replay needs the target repo present to re-run the filesystem reads. Fully hermetic replay (cassetting tool results too) is a clean extension I have left as a documented next step rather than built.
- **The benchmark is small.** Three cases is enough to prove the harness works and to catch a regression, not enough to make a strong claim about accuracy. Growing it is the cheapest way to make the numbers mean more.

## Where I would take it next

Parallel execution of independent tasks is the obvious performance win, with the cassette-keying change that comes with it. After that: durable run and audit storage so the API survives a restart, a bigger benchmark so the accuracy numbers carry more weight, and a second example task beyond security auditing. That last one matters most, because the planner, supervisor, orchestrator, and memory are all task-agnostic by design, and a second task is what would prove it rather than just claiming it. The security audit is the demonstration, not the product.

## License

MIT. See `LICENSE`.
