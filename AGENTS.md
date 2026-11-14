# AGENTS.md

## What this repo is

InfraSage: a Bayesian troubleshooting system that diagnoses Linux/Docker
infrastructure incidents and abstains when it cannot tell. The diagnostic
packages are still stubs; the digital twin (`backend/twin/`, `lab/`) is
implemented through §18 step 4. Check a file before assuming code exists, and
build tooling rather than "fixing" something that looks broken.

## Read this first

`backend/twin/DIGITAL_TWIN_DEVELOPMENT_GUIDE.md` is the normative brief for the
digital twin (the Docker fault-injection lab that produces labeled calibration
and evaluation data). Where it conflicts with a design note, stop and ask
instead of choosing silently. Build in its §18 order; don't start a later step
before the earlier one passes its checks.

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
  state. The existing `CollectionOrchestrator` is preserved as the batch
  collection implementation behind `CollectorManager`.
- **Three catalogues**, linked by stable IDs:
  - `knowledge.yaml` — hypotheses + qualitative `supports`/`contradicts` relations
  - `tests.yaml` — executable bindings: provider, operation, outcomes, costs
  - `probabilities.json` — priors + likelihoods `P(outcome | hypothesis, test)`
- **Netdata is primary observer.** Docker/precision probes fill verified gaps only.
- **DiagnosticAdapter** separates raw collection from Bayes math. It produces
  `Observation` objects with `execution_status` (COMPLETED/FAILED/UNAVAILABLE),
  never converting errors into diagnostic outcomes.
- **Planner decides, orchestrator executes.** The planner returns a test ID;
  the orchestrator resolves it in `tests.yaml` and calls
  `CollectorManager.execute_test()`.
- **Ground truth isolation.** `backend/twin/` is never imported by diagnostic packages.

## Commands

`requirements.txt` holds the twin's Python dependencies (pytest, PyYAML). The
shared test command is:

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
  (`investigate(LabTarget) -> InvestigationResult`), still raising
  `NotImplementedError` — brief §21 Q3. Do not widen the twin's access to
  diagnostic internals; implement behind that signature.
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
