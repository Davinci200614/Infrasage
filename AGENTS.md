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
twin** (backend/twin/) runs offline calibration and later selective validation.

Key invariants:
- **One orchestrator.** No second "collection orchestrator" with investigation
  state. The existing `CollectionOrchestrator` is preserved as the batch
  collection implementation behind `CollectorManager`.
- **Three catalogues**, linked by stable IDs:
  - `knowledge.yaml` — hypotheses + qualitative `supports/contradicts` relations
  - `tests.yaml` — executable bindings: provider, operation, outcomes, costs
  - `probabilities.json` — priors + likelihoods `P(outcome | hypothesis, test)`
- **Netdata is primary observer.** Docker/precision probes fill verified gaps only.
- **DiagnosticAdapter** separates raw collection from Bayes math. It produces
  `Observation` objects with `execution_status` (COMPLETED/FAILED/UNAVAILABLE),
  never converting errors into diagnostic outcomes.
- **Planner decides, orchestrator executes.** The planner returns a test ID;
  the orchestrator resolves it in `tests.yaml` and calls `CollectorManager.execute_test()`.
- **Ground truth isolation.** `backend/twin/` is never imported by diagnostic packages.

## Commands

`requirements.txt` holds the twin's Python dependencies (pytest, PyYAML). The
shared test command is:

```sh
.venv/bin/python -m pytest backend/twin/tests -q
```

(`python3 -m venv .venv && .venv/bin/pip install -r requirements.txt` to create
it. `.venv/` is git-ignored.) There is still no linter, CI workflow, lockfile, or
dev server — add a dependency to `requirements.txt` and record the new command
here if you introduce one. `opencode.json` is agent tooling (the ponytail
plugin), not app config.

Docker commands need the `docker` group. In a shell started before `usermod -aG
docker`, wrap them: `sg docker -c '<command>'`. The twin's CLI calls inherit
that group from whichever process runs the test or run script.


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
- One fault per run, one run at a time, full teardown (`docker compose -p
  infrasage-lab down -v`) between runs.
- Don't modify `frontend/` or the Voting App source for twin work; overrides go
  in `lab/docker-compose.infrasage.yml`.

## Safety

- Lab work needs `/var/run/docker.sock`, i.e. host-level root. Run it on a
  dedicated or disposable host, not a shared workstation or production machine.
  Docker's storage here is capped at 40 GiB (`/var/lib/docker` is an ext4 loop;
  see `lab/README.md`).
- The injector must refuse any container lacking the `infrasage-lab` Compose
  project label. Never point it at a real project.
- `.gitignore` excludes `.env`, `.venv/`, `experiments/runs/`,
  `experiments/aggregates/` and `experiments/ground_truth/`. Only `.env.example`
  is meant to be committed. Ground truth stays out of git.

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
- An earlier 3-member ownership map and a pre-guide directory tree live at
  `git show a0f5de1:README.md`; they are superseded by brief §17.1. Don't
  restructure toward them without asking.

## MCP servers (project-scoped in `opencode.json`)

| Server | Purpose | Connects to |
|---|---|---|
| `context7` | Library/framework docs (FastAPI, Docker SDK, React, etc.) | Remote (always on) |
| `docker` | Inspect/control the `infrasage-lab` Compose stack | `/var/run/docker.sock` (needs Docker installed + lab running) |
| `postgres` | Query the voting-app `db` (schema, data, verify faults) | `postgresql://postgres:postgres@localhost:5432/postgres` (needs lab DB running) |
| `redis` | Inspect Redis keys/pubsub, verify cache faults | `redis://localhost:6379` (needs lab Redis running) |
| `fs` | Read/write under repo root (`experiments/`, `lab/`, `backend/twin/`) | Local filesystem (scoped to repo) |
| `git` | Diff, status, commit, blame — cleaner than shell `git` | Local repo |
| `github` | Create PRs, read issues, link runs to issues | `~/.config/opencode/github.token` (a PAT) |

**To use the lab-dependent servers (docker, postgres, redis):**
1. Start the lab: `docker compose -p infrasage-lab -f lab/compose.yaml -f lab/docker-compose.infrasage.yml up -d`
2. URIs are hardcoded in `opencode.json` — opencode does not expand `{env:VAR}` inside
   command arguments. Postgres uses the `postgres` database (the worker's default);
   the lab has no `voting_app` database.
3. `redis`/`postgres` connect eagerly at startup. If the lab was down then, opencode
   marks them `failed: Request timed out` and never retries. Start the lab, then force
   a reconnect:
   ```sh
   opencode api post '/api/experimental/mcp/redis/connect?location[directory]=/home/divinesword/INDIA/Infrasage'
   opencode api post '/api/experimental/mcp/postgres/connect?location[directory]=/home/divinesword/INDIA/Infrasage'
   ```

**Notes:**
- `docker` server uses `mcp-docker-server` (not the `@modelcontextprotocol` one, which doesn't exist on npm)
- `postgres`/`redis` are `@modelcontextprotocol/server-*`; their URI is the last
  argument in `opencode.json`
- `.gitignore` must exclude `.env` (not committed)
- `fs` is scoped to this repo only; it cannot escape
- `github` reads its PAT from `~/.config/opencode/github.token` (chmod 600, no
  trailing newline) via `{file:...}`; opencode never reads `.env`. After writing
  the token, reconnect the server:
  ```sh
  opencode api post '/api/experimental/mcp/github/connect?location[directory]=/home/divinesword/INDIA/Infrasage'
  ```
