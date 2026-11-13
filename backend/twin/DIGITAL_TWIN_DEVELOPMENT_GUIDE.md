# InfraSage Digital Twin: Development Guide

This document is the implementation brief for building the **digital twin** subsystem of InfraSage. It is written for a coding agent or engineer who has not read the project design notes. Read it fully before writing code. Where this guide and the design note disagree, stop and raise the conflict rather than choosing silently.

---

## 1. Purpose

InfraSage is a troubleshooting system that diagnoses Linux and Docker infrastructure incidents. It reasons over evidence from collectors, maintains a probability distribution over candidate causes (including `other_unknown`), chooses the next diagnostic test, and reports a diagnosis or an abstention.

Measuring whether InfraSage is correct requires knowing the true cause of each incident. Real incidents do not come with reliable labels. The **digital twin** solves this. It is a disposable, controllable copy of a target system in which faults are injected deliberately, so the true cause is known exactly. Its jobs are:

1. **Provide ground truth.** Every injected fault is recorded with a label the diagnostic system never sees.
2. **Generate labeled data.** Repeated runs per fault produce the empirical test-outcome counts used to estimate likelihoods `P(outcome | cause, test)`.
3. **Provide an independent evaluation set.** Some runs are held out and never used for calibration.
4. **Validate repairs.** A proposed fix is applied in the twin, and the steady state is checked before the fix is recommended.
5. **Measure cost.** Test latency and energy are recorded under controlled conditions.

The twin is **not** a 3D or physics model. It is a running software stack.

---

## 2. Scope

### 2.1 In scope for V0.1

- A reproducible Docker Compose replica of the **Docker Example Voting App** (repository: `https://github.com/dockersamples/example-voting-app`). Services: `vote` (Python), `redis`, `worker` (.NET), `db` (PostgreSQL), `result` (Node.js). Verify exact image tags, ports, and environment variables against the pinned commit before relying on them.
- An **experiment runner** that resets the stack, starts a workload, injects one fault, runs InfraSage's collectors, records the outcome, and tears down.
- A **ground-truth store** that is physically and logically separated from the diagnostic system.
- A **workload generator** that keeps the system active during experiments.
- A **fault injector** with a fixed, versioned catalogue of faults.
- A **repair validator** that applies a candidate fix and checks steady-state recovery.
- **Run records** in a stable schema, suitable for estimating likelihoods and for evaluation.

### 2.2 Out of scope for V0.1

- Native (non-container) Linux application targets. Plan for them, but do not implement them yet.
- Continuous live mirroring of a production system. The twin is a controlled copy, not a synchronized replica.
- Automatic discovery of arbitrary topologies. The Voting App topology may be hardcoded in the twin's definition files.
- Changes to `frontend/`. The twin has no UI of its own.
- Claims of calibrated real-world accuracy. Twin results measure performance in the lab only.

---

## 3. Core principles

These are non-negotiable. Violating them invalidates the experimental results.

1. **Ground truth isolation.** The fault label must never be readable by the diagnostic process during an investigation. Store labels in a separate module with no path from the diagnostic code. Enforce this in code, not by convention. Add a test that fails if any diagnostic module imports the ground-truth module (see section 9).
2. **Same evidence path as production.** The twin runs the **same collector implementations** that InfraSage uses on real hosts, from `backend/collectors/`. Do not write separate "twin-only" probes that give the diagnostic engine different information. If a twin-only shortcut is needed, label it and exclude it from evaluation.
3. **Reproducible reset.** Every run must start from an identical state. A run that depends on residue from a previous run is invalid and must be discarded and logged.
4. **One fault per run.** V0.1 injects exactly one primary fault per run. Compound faults are a later extension and must be recorded with their full set of labels.
5. **Independent evaluation split.** Runs are assigned to `calibration` or `evaluation` before execution, using a fixed seed. The evaluation split is never used to estimate parameters or tune thresholds.
6. **Failure to execute is not a negative result.** A collector or injector error is recorded as `execution_status: error`, never as an observation outcome. An injector that failed to stop a container must not produce a run labeled as a successful fault.
7. **Honest energy reporting.** Energy values are labeled by method: `measured` (hardware counters), `estimated` (attribution model), or `not_measured`. Never present an estimate as a measurement.
8. **Versioning.** Fault definitions, workload definitions, collector versions, and the target stack version are all recorded with each run. Changing any of them creates a new version and invalidates affected calibration.

---

## 4. Architecture

```
+-----------------------------------------------------------------+
|                       Experiment Runner                          |
|  (orchestrates one run; owns the hidden ground-truth label)      |
+--------+------------+-------------+-------------+----------------+
         |            |             |             |
         v            v             v             v
  +-----------+ +------------+ +-----------+ +---------------+
  | Lab Env   | | Workload   | | Fault     | | Ground-truth  |
  | Manager   | | Generator  | | Injector  | | Store         |
  | (reset,   | | (votes)    | | (apply/   | | (hidden;      |
  |  up, down)| |            | |  revert)  | |  write-only   |
  +-----+-----+ +-----+------+ +-----+-----+ |  during run)  |
        |             |              |       +---------------+
        v             v              v
  +-----------------------------------------------+
  |  Target stack: vote, redis, worker, db, result |
  |  (Compose project: infrasage-lab, from lab/)   |
  +-----------------------+-----------------------+
                          |
                          v
  +-----------------------------------------------+
  |  InfraSage diagnostic system (UNDER TEST)      |
  |  backend/collectors -> normalizer ->           |
  |  backend/diagnosis -> planner -> report        |
  |  (or abstain)                                  |
  |  NO access to backend/twin/ground_truth        |
  +-----------------------+-----------------------+
                          |
                          v
  +-----------------------------------------------+
  |  Run record -> experiments/runs/ (calibration  |
  |  or evaluation split)                          |
  +-----------------------------------------------+
```

### 4.1 Component responsibilities

| Component | Location | Responsibility | Must not |
|---|---|---|---|
| Experiment Runner | `backend/twin/runner/` | Sequences a run: reset, start, warm up, inject, investigate, record, score, tear down. | Pass the fault label into the diagnostic call. |
| Lab Env Manager | `backend/twin/runner/lab.py` | Creates and destroys the Compose project defined in `lab/`; verifies health before and after. | Share state across runs. |
| Workload Generator | `backend/twin/workload/` | Submits votes at a configured rate; records submissions. | Hit anything except the `vote` frontend. |
| Fault Injector | `backend/twin/injector/` | Applies and reverts one fault from the catalogue; records what it actually did. | Report success without verifying the fault took effect. |
| Ground-Truth Store | `backend/twin/ground_truth/` | Holds the injected fault label, injection timestamp, and parameters. | Be importable from diagnostic code. |
| Diagnostic System | `backend/api`, `backend/collectors`, `backend/core`, `backend/diagnosis`, `backend/optimization`, `backend/energy` | Runs InfraSage unmodified against the twin. | Import twin internals or ground truth. |
| Scorer | `backend/twin/scoring/` | Compares the frozen diagnosis to the label. | Run before the diagnosis is frozen. |

---

## 5. Target stack setup

### 5.1 Pin the target

- Clone the Voting App at a **pinned commit**. Record the commit hash in `lab/target.lock`.
- Use the repository's `docker-compose.yml` as the base. Do not modify service code. Configuration changes (for example, health checks, fixed ports, or the wrong-hostname fault) go in an override file, `lab/docker-compose.infrasage.yml`, so the base file stays untouched.
- Use a **fixed Compose project name**: `infrasage-lab`. Start with `docker compose -p infrasage-lab -f <base> -f lab/docker-compose.infrasage.yml up -d`.
- Pin image tags. Do not use `latest`.
- Keep the base Compose file itself in `lab/` (for example, `lab/voting-app/`, a checkout or vendored copy at the pinned commit). Decide whether to vendor it or clone it in a setup script; record the choice in `lab/README.md`.

### 5.2 Network and naming

- Use the Compose-generated network under the `infrasage-lab` project. Do not attach lab containers to any other network.
- Identify containers by Compose labels (`com.docker.compose.project`, `com.docker.compose.service`), never by generated name.
- Expose only the ports needed for the workload generator and for the user-facing frontends. Bind them to `127.0.0.1` to avoid exposing the lab on a network interface.

### 5.3 Health baseline

Before any run, the Lab Env Manager must verify a **steady state**:

- All five services running.
- Redis reachable from the worker's network.
- PostgreSQL accepting connections.
- A test vote submitted through `vote` appears in the `result` output within a configured timeout.

If the baseline check fails, the run is marked `invalid_baseline` and is not counted. Log the failure and do not inject.

---

## 6. Workload generator

Without traffic, most faults produce no observable symptom, and the diagnostic system has nothing to explain.

- Submit votes at a configured rate (for example, 2 votes per second) to the `vote` frontend over HTTP.
- Use a deterministic sequence (fixed seed) so runs are comparable.
- Record every submission with a timestamp and an identifier.
- Start the workload before injecting the fault, and let it run for a warm-up period (configurable, default 30 s).
- Keep the workload running through the investigation.
- Stop it during teardown.

The workload generator must be a separate process from the fault injector and the diagnostic system.

Workload definitions live in `backend/twin/catalogues/workloads.yaml`.

---

## 7. Fault catalogue

Faults are defined in a versioned file, `backend/twin/catalogues/faults.yaml`. Each entry is a fixed contract. Do not add ad hoc faults inside experiment scripts.

### 7.1 Required schema

```yaml
faults:
  redis_process_stopped:
    version: 1
    target_service: redis
    injection:
      method: docker_stop           # docker_stop | docker_pause | network_disconnect | ...
      parameters: {}
    revert:
      method: docker_start
    expected_symptom: "vote accepted, result does not update"
    label_component: redis
    label_cause: redis_process_stopped
    applicable_to_v0_1: true
```

### 7.2 Initial fault set (from the design note)

Candidate hypotheses for the first experiment:

| Label | Injection method (suggested) | Distinguishes from |
|---|---|---|
| `redis_process_stopped` | `docker stop redis` | `redis_network_unreachable` |
| `redis_network_unreachable` | disconnect `redis` from the lab network, or apply a netfilter drop rule for the Redis port | `redis_process_stopped` |
| `postgres_unavailable` | stop `db` | worker-only faults |
| `worker_process_stopped` | stop `worker` | Redis or DB faults |
| `connectivity_or_endpoint` | set a wrong Redis/DB hostname in the worker environment via the override file and restart | process faults |
| `other_unknown` | **no injected modeled fault**; reserved for runs that inject an unmodeled condition (see 7.3) | all modeled faults |

The design note requires at least one probe that distinguishes a stopped service from a network issue. Verify that the chosen injection methods actually produce that distinction before relying on them.

### 7.3 Unknown-cause runs

The `other_unknown` hypothesis must be exercised, or the abstention behavior is untested. Include a small set of **unmodeled** faults that the diagnostic system has no hypothesis for, for example:

- Filling the disk in the `db` container.
- Throttling CPU of the `worker` with `docker update --cpus`.
- Injecting latency with `tc netem` in the `redis` network namespace.

These runs are labeled `label_cause: other_unknown` and are used to evaluate abstention. They must **not** be used to estimate likelihoods for modeled causes.

### 7.4 Fault applicability

A fault is applicable only if:

- its injection has been **verified to take effect** (for example, `docker inspect` shows `running: false` after `docker stop`), and
- the diagnostic system has a test that can observe its symptom.

The injector must perform its own verification and return `injection_status: verified` or `injection_status: failed`. Runs with `failed` injection are excluded from calibration and reported separately.

---

## 8. Experiment runner

### 8.1 Run lifecycle

```
1. assign run_id, split (calibration|evaluation), seed     [before any action]
2. reset lab (down -v, up -d)
3. verify steady-state baseline                            -> fail: invalid_baseline, stop
4. start workload generator; warm up
5. inject fault via Fault Injector; verify injection       -> fail: injection_failed, stop
6. write ground-truth record (label, injection time, params)
7. invoke InfraSage investigation (no label access)
8. freeze diagnosis: record posterior, decision, tests run, costs
9. score: compare decision to label
10. revert fault; stop workload; tear down
11. persist run record to experiments/runs/
```

Step 6 must happen **after** step 5 verifies the fault and **before** step 7, and the label must be written to the store that the diagnostic process cannot read.

Step 8 must complete before step 9. Scoring a run before the diagnosis is frozen is a bug.

### 8.2 Timeouts

Every step has a timeout. A timeout produces `execution_status: error` for that step and is recorded. Do not retry silently. Retries, if any, must be recorded as separate attempts.

### 8.3 Concurrency

V0.1 runs **one experiment at a time** on one lab. Parallel runs share Docker state and invalidate isolation. Run them sequentially. Parallelism across independent hosts can be added later.

### 8.4 Teardown and residue

After each run, tear down the project with volumes removed (`docker compose -p infrasage-lab down -v`). Verify that no lab containers, volumes, or networks remain. If teardown fails, mark the lab as `dirty` and refuse to start another run until it is rebuilt.

---

## 9. Ground truth and separation

This is the most important correctness property in the subsystem.

- The ground-truth store writes the label **once per run**, before investigation, and is closed to reads by the diagnostic process.
- The diagnostic modules are `backend/api`, `backend/collectors`, `backend/core`, `backend/diagnosis`, `backend/optimization`, and `backend/energy`. **None of them may import `backend.twin`.** Enforce this with a test that inspects imports across these packages (`backend/twin/tests/test_isolation.py`).
- `backend/energy` is shared between the diagnostic planner (which uses predicted test energy) and the twin (which measures it). The shared code must be pure measurement and estimation utilities, with no fault or label knowledge.
- Scoring happens in the twin's process, after the diagnosis is frozen, and reads the label then.
- Logs produced during investigation must not include the label. Check log output in tests.

Store the label in a file or table with restricted permissions, separate from the diagnostic output directory.

---

## 10. Evidence and the diagnostic interface

The twin does not implement its own diagnostic logic. It calls InfraSage's public investigation entry point. The exact location and signature must be confirmed in the repository (open question 3). The call has the form:

```python
result = investigate(target=lab_target_descriptor)
```

`lab_target_descriptor` identifies the target by Compose project and service labels. It must not contain the fault label, the fault name, or any hint about the injected fault.

The run record captures what the diagnostic system produced:

- Each test executed, with its normalized outcome (for example `connected`, `refused`, `timeout`, or `execution_error`), its timestamp, and its latency.
- The posterior over causes at each step, if the engine exposes it.
- The final decision: a cause or `abstain`.
- Total cost: number of tests, elapsed time, and energy if measured or estimated.

If the diagnostic system needs a change to support the twin (for example, a missing public entry point), change the diagnostic API in `backend/api` or `backend/core`, not the twin's access to internals.

---

## 11. Run record schema

Every run produces one record. The schema is stored at `experiments/schema/run_record.schema.json` and each run is written to `experiments/runs/<run_id>.json`. Minimum fields:

```json
{
  "schema_version": "1.0",
  "run_id": "redis-stop-0001",
  "split": "calibration",
  "seed": 421,
  "scenario": "redis_process_stopped",
  "fault_version": 1,
  "environment": "voting-app-v0.1",
  "target_commit": "<pinned commit hash>",
  "override_file_hash": "<sha256 of lab/docker-compose.infrasage.yml>",
  "lab_status": "valid",
  "injection": {
    "method": "docker_stop",
    "status": "verified",
    "injected_at": "<ISO-8601>"
  },
  "workload": {
    "rate_per_second": 2,
    "warmup_seconds": 30,
    "votes_submitted": 0
  },
  "diagnosis": {
    "decision": "redis_process_stopped",
    "abstained": false,
    "posterior": {"redis_process_stopped": 0.0, "redis_network_unreachable": 0.0, "other_unknown": 0.0},
    "tests": [
      {
        "test_id": "redis_connectivity_from_worker",
        "outcome": "refused",
        "execution_status": "completed",
        "observed_at": "<ISO-8601>",
        "collector_version": "<commit-or-version>",
        "latency_ms": 0,
        "energy_joules": null,
        "energy_method": "not_measured",
        "raw_reference": "experiments/runs/redis-stop-0001/raw/redis_connectivity_from_worker.json"
      }
    ],
    "total_tests": 1,
    "elapsed_seconds": 0
  },
  "ground_truth_label": "redis_process_stopped",
  "scored": {
    "correct": true,
    "abstained": false
  }
}
```

Notes:

- `ground_truth_label` appears in the record **only after** diagnosis is frozen.
- `lab_status` may be `valid`, `invalid_baseline`, `injection_failed`, `dirty`, or `error`. Only `valid` runs with verified injection enter calibration or evaluation.
- Raw collector output is stored under `experiments/runs/<run_id>/raw/`, so outcomes can be re-derived if a normalization rule changes.

---

## 12. Repair validator

The twin can test whether a proposed repair restores normal operation before the repair is recommended.

### 12.1 Procedure

1. Run the fault scenario to the point where a fault is present and diagnosed.
2. Apply the candidate repair action from the action catalogue `backend/twin/catalogues/repairs.yaml` (for example, `docker start redis`, or revert a wrong hostname in the override file).
3. Re-run the steady-state check from section 5.3, including the end-to-end vote test.
4. Record `repair_status`: `restored`, `not_restored`, `repair_failed` (action could not execute), or `timeout`.

### 12.2 Rules

- Repair actions come from the **catalogue**, not free-form commands. The validator must refuse actions not in the catalogue.
- A repair is only "validated" if steady state returns **within the timeout**, not merely if the command exits successfully.
- Record repair cost separately from diagnostic cost. Repair experiments can be much more expensive than probes.
- Do not use repair runs to estimate diagnostic likelihoods.
- The repair action is a *recommendation* produced by InfraSage. Applying it in the twin tests the recommendation; it does not authorize applying it to a real system. The twin must never call the repair path against a non-lab project.

---

## 13. Cost and energy measurement

- Record per test: `latency_ms`, and energy as `energy_joules` with `energy_method` set to `measured`, `estimated`, or `not_measured`.
- Measure **baseline** host energy separately, without any lab running and without the diagnostic system running, so incremental costs can be attributed.
- Measure the incremental cost of each collector type under controlled load.
- If hardware counters are available (for example, Intel RAPL through a tool such as `pyJoules` or `Scaphandre`), prefer them. Verify availability on the host first; many VMs do not expose them.
- Record the hardware, OS, and background load alongside every energy value.
- Do not total energy across measurement methods without reporting the method breakdown.
- Energy helpers used by both the twin and the planner live in `backend/energy/`. Twin-specific measurement orchestration lives in `backend/twin/`.

---

## 14. Calibration and evaluation outputs

The twin produces the data consumed by the Bayesian estimator in `backend/diagnosis/`. Its outputs are:

- **Calibration runs** (`split: calibration`): used to estimate `P(outcome | cause, test)` using the smoothed estimator

  `P(o | H, T) = (N[H,T,o] + alpha) / (N[H,T] + K * alpha)`

  where `K` is the number of outcomes for test `T` and `alpha` is a documented smoothing hyperparameter.

- **Evaluation runs** (`split: evaluation`): used only to report accuracy, abstention behavior, calibration, and cost. Never used to fit parameters.

Requirements:

- Each likelihood entry must carry its sample count `N[H,T]`. Entries with low counts must be flagged as weak.
- Counts must come from independent runs. Repeating an identical deterministic run many times does not give independent evidence. Vary timing, workload rate, and configuration across runs.
- Record which tests were executed in each run. If the planner chooses tests adaptively, the data has selection bias. For estimation, the calibration protocol must execute a **fixed core test set** for every injected cause.
- Priors must not be estimated from balanced injection frequencies, since injections are an experimental sampling plan, not real failure rates.
- Aggregated outputs (counts, likelihood tables, reports) are written to `experiments/aggregates/`.

---

## 15. Validation of the twin itself

Before the twin's results are trusted, the twin must pass these checks.

| Check | Pass condition |
|---|---|
| Reproducibility | The same seed and scenario, run 5 times, produce identical injection parameters and comparable baseline checks. |
| Reset correctness | After teardown, no lab containers, volumes, or networks remain. A new baseline check passes. |
| Injection verification | For every fault, the verification step detects the injected state in 100% of attempts on the reference host. |
| Fault-symptom coupling | Each modeled fault produces its expected symptom in the workload (for example, votes stop appearing in results). Record misses. |
| Isolation | Lab containers are not reachable from unrelated containers on the host network, and the diagnostic system's logs contain no ground-truth label. |
| Label separation | Automated test confirms no diagnostic module imports `backend.twin` (section 9). |
| Determinism of seeds | Splits and workload sequences are reproducible from the seed. |

Keep the results of these checks in `experiments/validation/`.

---

## 16. Security

- **The Docker socket is host-level privilege.** Any process with access to `/var/run/docker.sock` can control the host. Run the twin on a dedicated VM or disposable host. Do not run it on a shared workstation or production host.
- The twin's injector needs Docker control. The diagnostic collectors need read access to container state. If possible, give the diagnostic collectors a read-only path; document the permissions that remain.
- Never expose the lab's frontend ports beyond localhost. Never commit credentials. Secrets and host-specific paths go in `.env` (copied from `.env.example`), which must stay out of git. The Voting App uses default database credentials; keep the lab isolated accordingly.
- Injected network faults (for example, `tc netem`, iptables rules) must be removed on revert and on teardown. Include them in the reset procedure.
- Fault injection must only target containers with the `infrasage-lab` Compose project label. Refuse to act on any other container, and test this guard.

---

## 17. Repository mapping

The repository already has this top-level layout:

```
Infrasage/
  backend/
    api/            # diagnostic API (must not import twin)
    collectors/     # evidence collectors (reused by the twin)
    core/           # core diagnostic types and logic
    diagnosis/      # Bayesian engine, likelihood estimation
    energy/         # energy measurement/estimation helpers (shared)
    optimization/   # test-selection planner
    twin/           # digital twin subsystem (this guide)
    __init__.py
    main.py
  frontend/         # UI (out of scope for the twin)
  lab/              # target stack and lab-host configuration (this guide)
  experiments/      # run records, aggregates, validation output (this guide)
  .env.example
  .gitignore
  README.md
  requirements.txt
```

Some inner contents of `backend/` subfolders were not inspected when this guide was written. Check each folder for existing code before creating files, and reuse rather than duplicate.

### 17.1 Where each twin artifact goes

| Artifact | Location |
|---|---|
| Pinned target commit and image digests | `lab/target.lock` |
| Compose base checkout or vendoring notes | `lab/voting-app/`, `lab/README.md` |
| Compose override (health checks, fixed ports, fault-only overrides) | `lab/docker-compose.infrasage.yml` |
| Fault catalogue | `backend/twin/catalogues/faults.yaml` |
| Workload definitions | `backend/twin/catalogues/workloads.yaml` |
| Repair action catalogue | `backend/twin/catalogues/repairs.yaml` |
| Experiment runner | `backend/twin/runner/` (`experiment.py`, `lab.py`, `splits.py`) |
| Workload generator | `backend/twin/workload/` |
| Fault injector | `backend/twin/injector/` |
| Repair validator | `backend/twin/repair/` |
| Ground-truth store | `backend/twin/ground_truth/` |
| Scoring and aggregation | `backend/twin/scoring/` |
| Twin self-tests (isolation, guards, teardown, schema) | `backend/twin/tests/` |
| Run record JSON schema | `experiments/schema/run_record.schema.json` |
| Run records and raw collector output | `experiments/runs/` |
| Likelihood tables and reports | `experiments/aggregates/` |
| Twin self-validation results | `experiments/validation/` |
| Twin Python dependencies | `requirements.txt` at the root, or `backend/twin/requirements.txt` if the dependencies must be isolated |
| Environment variables (paths, hosts, credentials) | `.env` (from `.env.example`), never committed |

### 17.2 Rules for this layout

- `backend/twin/` may import from `backend/collectors`, `backend/energy`, and the public diagnostic API. The reverse is forbidden.
- `lab/` contains configuration and the target stack only. It contains no Python diagnostic or twin logic.
- `experiments/` contains data only. Code that produces it lives in `backend/twin/`. Keep large raw outputs out of git (add `experiments/runs/` and `experiments/aggregates/` to `.gitignore` unless a sample is intentionally committed).
- Do not modify `frontend/` for the twin.

---

## 18. Build order

Implement in this order. Do not start a later step until the earlier one passes its checks. Steps 1 to 4 can be started immediately; step 6 depends on confirming the investigation entry point (open question 3).

1. **Lab manager.** Pin the target in `lab/target.lock`, bring the stack up and down under `infrasage-lab`, and implement the baseline check in `backend/twin/runner/lab.py`. Pass: five consecutive reset-up-check-down cycles with no residue.
2. **Workload generator.** Submit votes in `backend/twin/workload/`; confirm end-to-end propagation to `result` in the baseline. Pass: baseline vote appears within the timeout in 10 of 10 trials.
3. **Fault injector with verification.** Implement the two simplest faults first: `redis_process_stopped` and `postgres_unavailable`, in `backend/twin/injector/`. Verify injection and revert. Pass: verified injection in every trial, clean revert.
4. **Ground-truth store and separation test.** Implement `backend/twin/ground_truth/` and the import-guard test in `backend/twin/tests/test_isolation.py` before connecting any diagnostic code.
5. **Experiment runner.** Wire steps 1 to 11 of section 8.1 in `backend/twin/runner/`. Use a stub diagnostic that returns a fixed answer, to test the pipeline independently of InfraSage.
6. **Connect InfraSage.** Replace the stub with the real investigation entry point. Do not change the interface to suit the twin.
7. **Reuse existing collectors.** Check `backend/collectors/` first. Wrap what exists with the normalized test contract (section 4 of the design note: `available_tests`, `execute_test`, `TestResult`) rather than writing new probes.
8. **Remaining faults.** Add `worker_process_stopped`, `redis_network_unreachable`, `connectivity_or_endpoint`, then the unmodeled faults for `other_unknown`.
9. **Run records and scoring.** Write the schema to `experiments/schema/`, validate every record against it, and produce per-fault counts in `experiments/aggregates/`.
10. **Calibration and evaluation split.** Assign splits by seed before running. Generate the calibration batch and a separate evaluation batch.
11. **Repair validator.** Implement after diagnostic experiments are stable.
12. **Energy measurement.** Add after latency measurement is reliable. Record baseline first, in `backend/energy/` and orchestrated from `backend/twin/`.
13. **Twin self-validation.** Run the checks in section 15 and store the results in `experiments/validation/`.

---

## 19. Acceptance criteria for V0.1

The digital twin is ready for calibration when all of the following hold:

- [ ] The target is pinned by commit and image digest, and `lab/target.lock` is committed.
- [ ] A single command runs one full experiment end-to-end and writes one valid run record to `experiments/runs/`.
- [ ] Each modeled fault has verified injection and a documented symptom.
- [ ] Unmodeled `other_unknown` faults are implemented and labeled.
- [ ] The ground-truth isolation test passes in CI.
- [ ] Reset leaves no residue after 20 consecutive runs.
- [ ] Every run record validates against `experiments/schema/run_record.schema.json`.
- [ ] Calibration and evaluation splits are assigned by seed before any run.
- [ ] Energy values carry an explicit `energy_method`.
- [ ] Twin self-validation results (section 15) are stored and reviewed.
- [ ] No Docker socket exposure beyond the dedicated lab host.
- [ ] `.env` and any credentials are excluded by `.gitignore`.

---

## 20. Known risks and how to handle them

| Risk | Handling |
|---|---|
| Injected faults do not match production failures | Document the mismatch; treat results as lab performance, not field accuracy. Include unmodeled faults. |
| Injection silently fails | Verify every injection; record `injection_failed`; exclude from calibration. |
| Residue between runs corrupts results | Full teardown with volumes; residue check; mark `dirty` and rebuild. |
| The diagnostic system learns the label through logs or cache | Separate module, log scrubbing check, cache cleared per run. |
| Overfitting to the lab | Vary timing, load, and configuration; keep the evaluation split untouched. |
| Correlated test outcomes inflate confidence | Record dependencies; evaluate conditional independence before trusting posteriors. |
| Energy numbers mistaken for measurements | Mandatory `energy_method` field; reporting template shows method breakdown. |
| Voting App upstream changes | Pin commit and digests in `lab/target.lock`; re-run validation when the pin changes. |
| Repairs applied outside the catalogue | Validator refuses unknown actions. |
| Twin code leaks into diagnostic code | Import-guard test over the six diagnostic packages in section 9. |

---

## 21. Open questions to resolve before or during implementation

These are not decided by the design notes or the current repository layout. Ask before implementing the affected part.

1. Which host will run the twin: a dedicated VM, a bare-metal machine, or a CI runner? This determines energy measurement options and Docker socket exposure.
2. Will the diagnostic system read Netdata data for evidence, and if so, will the twin run a Netdata Agent inside the lab host? The design note assumes Netdata is the primary observation layer.
3. Where is the investigation entry point (`backend/main.py`, `backend/api`, `backend/core`, or `backend/diagnosis`), what is its exact signature, and does it expose the posterior at each step?
4. What smoothing hyperparameter `alpha` and what minimum sample count per likelihood entry will be used?
5. What timeout values apply to the baseline vote, injection, and investigation steps?
6. Are unmodeled `other_unknown` faults acceptable to run against the same lab, or do they need a separate environment to avoid contaminating modeled-cause data?
7. Will repair actions be executed in the same lab after diagnosis, or in a freshly rebuilt lab?
8. Does `backend/twin/` already contain code, and if so, which parts of this guide does it already satisfy?
9. What is in `lab/` and `experiments/` today? If they already hold a Compose file or run output, this guide should build on them rather than replace them.

---

## 22. Non-goals

To keep the scope honest, the twin does **not**:

- Claim that its fault distribution reflects real-world incident frequencies.
- Estimate production accuracy from lab accuracy without a separate field validation step.
- Modify Voting App source code.
- Modify `frontend/`.
- Replace the diagnostic engine's validation on held-out real incidents when those become available.
- Provide a general-purpose chaos engineering platform.

---

## 23. Reference reading

- Design note: *Collectors, Catalogues & Bayesian Diagnosis* (InfraSage V0.1). Sections 08 to 11 and 13 define the calibration and lifecycle requirements this guide implements.
- Chaos engineering principles: steady-state hypothesis, varied real-world events, controlled experiments, and automation. The principles are in the Devoxx talk by Lorin Hochstein, listed in the project notes.
- ARCHES digital twin prototype (identical software on physical and digital twins; environment flag): https://arxiv.org/pdf/2103.08469
- Synchronization in digital twins for industrial control systems: https://arxiv.org/pdf/2006.03447
- TwinLoop (simulation-in-the-loop twins for policy rehearsal): https://arxiv.org/pdf/2604.06610
- Docker Engine API: https://docs.docker.com/reference/api/engine/
- Docker SDK for Python: https://docker-py.readthedocs.io/
- Docker Example Voting App: https://github.com/dockersamples/example-voting-app
