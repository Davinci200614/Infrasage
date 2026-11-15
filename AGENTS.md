# AGENTS.md

## What this repo is

InfraSage: a Bayesian troubleshooting system that diagnoses Linux/Docker
infrastructure incidents and abstains when it cannot tell. The diagnostic
backend is implemented; the digital twin (`backend/twin/`, `lab/`) is complete
through step 13 of its build order — `backend/twin/STATUS.md` records the last
build-order step that passed. Check a file before assuming code exists, and
build tooling rather than "fixing" something that looks broken.

## Read this first

`backend/twin/DIGITAL_TWIN_DEVELOPMENT_GUIDE.md` is the normative brief for the
digital twin (the Docker fault-injection lab that produces labeled calibration
and evaluation data). Where it conflicts with the Architecture section below,
stop and ask instead of choosing silently. Build in its §18 order; don't start a
later step before the earlier one passes its checks.

`backend/twin/STATUS.md` is the phase tracker: what passed, the evidence, open
findings, and the next session's scope. Update it when a phase passes.

The agreed V0.1 integration contracts (one InvestigationOrchestrator,
CollectorManager facade, three catalogues, DiagnosticAdapter/Normalizer) are
summarized in the Architecture section below.

## Architecture (V0.1 target)

InfraSage runs as **one Python backend** with a single **InvestigationOrchestrator**
owning the diagnostic lifecycle. A thin **CollectorManager** dispatches to
providers (Netdata primary, Docker precision). A **DiagnosticAdapter** normalizes
raw results to canonical test outcomes. The **Bayesian engine** updates beliefs;
the **energy-aware planner** chooses the next test/stop/abstain. The **digital
twin** (`backend/twin/`) runs offline calibration and later selective validation.

Key invariants:

- **One orchestrator.** No second "collection orchestrator" with investigation
  state. The teammate's `CollectionOrchestrator` (merged from
  `feature/docker-collector`) is the batch collection implementation behind
  `CollectorManager`.
- **Three catalogues**, linked by stable IDs:
  - `knowledge.yaml` — hypotheses + qualitative `supports`/`contradicts` relations
  - `tests.yaml` — executable bindings: provider, operation, outcomes, costs
  - `probabilities.json` — priors + likelihoods `P(outcome | hypothesis, test)`
- **Netdata is primary observer.** Docker/precision probes fill verified gaps only.
- **DiagnosticAdapter** separates raw collection from Bayes math. It produces
  `Observation` objects whose `execution_status` is `completed` or `error` —
  matching the `ExecutionStatus` type in `backend/api/investigate.py` — and never
  converts errors into diagnostic outcomes. `UNAVAILABLE` is a target-design
  value not yet in that enum; don't emit it until the type allows it.
- **Planner decides, orchestrator executes.** The planner returns a test ID;
  the orchestrator resolves it in `tests.yaml` and calls
  `CollectorManager.execute_test()`.
- **Ground truth isolation.** `backend/twin/` is never imported by diagnostic packages.

## Collectors

`CollectorManager` (in `backend/collectors/`) exposes two methods — this is the
whole contract the orchestrator and the twin use:

```python
collect_snapshot(target) -> dict          # batch: metrics + container state
execute_test(test_definition, target)     # on-demand: one probe -> Observation
```

Target providers: Netdata (primary metrics) and Docker (container state, health,
ports, restarts, CPU/memory). Optional additions: `docker_logs.py` and
`docker_events.py`.

Logs are **on-demand only** — never part of the initial batch snapshot. They
belong behind `execute_test()`, not `collect_snapshot()`. The events collector
is deferred; confirm it adds discriminating evidence before building it.

## Commands

`requirements.txt` holds the twin's Python dependencies (pytest, PyYAML,
psutil, pyJoules, docker, requests, jsonschema). The shared test command is:

```sh
.venv/bin/python -m pytest backend/twin/tests -q
```

(`python3 -m venv .venv && .venv/bin/pip install -r requirements.txt` to
create it. `.venv/` is git-ignored.) There is still no linter, CI workflow,
lockfile, or dev server — add a dependency to `requirements.txt` and record the
new command here if you introduce one.

Docker commands need the `docker` group. If your shell started before
`usermod -aG docker`, wrap Docker commands:

```sh
sg docker -c '<command>'
```

The twin's CLI calls inherit the group from whichever process runs the test or
run script.

## Invariants that are easy to break

- `backend/api`, `collectors`, `core`, `diagnosis`, `optimization`, `energy`
  must never import `backend.twin` or the ground-truth store. Write the import
  guard (`backend/twin/tests/test_isolation.py`) before connecting diagnostic
  code to the twin.
- The twin reuses the real collectors in `backend/collectors/`; no twin-only
  probes that give the engine different evidence.
- A collector or injector failure is `execution_status: error`, never an
  observed outcome. The fault label is written only after injection is verified
  and must never reach diagnosis (not even in logs) before the result is frozen.
- Every energy value carries `energy_method`: `measured`, `estimated`, or
  `not_measured`. Never present an estimate as a measurement.
- One fault per run, one run at a time, full teardown
  (`docker compose -p infrasage-lab down -v`) between runs.
- Don't modify `frontend/` or the Voting App source for twin work; overrides go
  in `lab/docker-compose.infrasage.yml`.

## Safety

- Lab work needs `/var/run/docker.sock`, i.e. host-level root. Run it on a
  dedicated or disposable host, not a shared workstation or production machine.
  See `lab/README.md` for host setup (storage cap, containerd root).
- The injector must refuse any container lacking the `infrasage-lab` Compose
  project label. Never point it at a real project.
- `.gitignore` excludes `.env`, `.venv/`, `experiments/runs/`,
  `experiments/aggregates/` and `experiments/ground_truth/`. Only `.env.example`
  is meant to be committed. Ground truth stays out of git.
- Never commit credentials. Copy `.env.example` → `.env` locally for lab ports
  and hosts; it must stay out of git.

## Unresolved, ask before inventing

- The investigation entry point is `backend/api/investigate.py`
  (`investigate(LabTarget) -> InvestigationResult`), now fully implemented.
- The other §21 open questions (lab host, Netdata, `alpha`, timeouts) apply to
  whatever part you touch.

## Layout

- Artifact locations (catalogues, runner, injector, ground truth, scoring, run
  schema) are fixed in brief §17.1. Follow them; don't invent parallel paths.
- `experiments/` holds data only; code that produces it lives in
  `backend/twin/`.

## To start the lab

```sh
docker compose -p infrasage-lab -f lab/compose.yaml -f lab/docker-compose.infrasage.yml up -d
```

The lab runs on localhost ports (bound to `127.0.0.1` in the override). Verify
health before injecting any fault: all five services running, Redis reachable,
PostgreSQL accepting connections, and a test vote appears in the `result`
output.

## Subagents

When writing code or advancing the project, spawn a relevant subagent for
verification tasks:

- **tester**: write/run tests for new code (do not change application code)
- **reviewer**: review changes for bugs, safety, performance (read-only)
- **explorer**: fast search across the codebase for patterns, symbols, refs
- **general**: multi-step research or parallel tasks

Use the `subagent` tool with `background=false` (default) for sequential work.
Each subagent runs in a fresh context — include all relevant context in the
prompt. Subagents are ephemeral; they exit on completion.