# Digital twin status

Phase tracker for `backend/twin/DIGITAL_TWIN_DEVELOPMENT_GUIDE.md` §18. Update
this file when a phase passes: it is the handoff for the next coding session.

Host: this device (Docker 29.9.3, Compose v5.7.1). Lab data is capped by the
40 GiB ext4 loop mounted at `/var/lib/docker` (see `lab/README.md`).

## Phase 1 — scaffolding and pinned lab stack (done)

- `opencode.json`: ponytail plugin + MCP servers.
- `AGENTS.md`, `.gitignore`, `.env.example`, `requirements.txt`, `.venv/`.
- Lab vendored at `63e9150` (`lab/target.lock`); upstream compose kept verbatim,
  changes only in `lab/docker-compose.infrasage.yml`; ports loopback-only.
- `backend/api/investigate.py`: Q3 contract shim, still `NotImplementedError`.
- `backend/twin/catalogues/workloads.yaml`: timeouts + workload definition.

## Phase 2 — lab manager, workload, injector, ground truth (done 2026-11-13)

| §18 step | Artifact | Pass criterion | Result |
|---|---|---|---|
| 1 Lab manager | `backend/twin/runner/lab.py` | 5 × reset→baseline→down, no residue | **pass** (5/5) |
| 2 Workload | `backend/twin/workload/generator.py` | baseline vote within timeout, 10/10 | **pass** (10/10) |
| 3 Injector | `backend/twin/injector/injector.py` | injection verified, clean revert | **pass** (4/4 trials) |
| 4 Ground truth | `backend/twin/ground_truth/store.py`, `tests/test_isolation.py` | write-once `0600`, import guard | **pass** (unit tests) |

Evidence:

```sh
.venv/bin/python -m pytest backend/twin/tests -q      # 19 passed
```

- Step 1: real build + 5 cycles, `PASS: 5 clean cycles`, residue empty every time.
- Step 2: `10/10 baseline votes landed` (each a unique voter_id found in `votes`).
- Step 3: `redis_process_stopped` and `postgres_unavailable`, 2 trials each —
  `inject=verified`, symptom observed, `revert=verified`, stack healthy again.
- Step 4: label file is `0600` and refuses a second write; AST guard rejects any
  `backend.twin` import in `api/collectors/core/diagnosis/optimization/energy`.

Faults implemented: `docker_stop` / `docker_start` for `redis` and `db` only.

## Next session — Phase 3: experiment runner (§18 step 5)

Do not start step 6 before step 5 passes.

1. `backend/twin/runner/experiment.py`: implement §8.1 steps 1–11 with a
   **stub diagnostic** (fixed answer; must not read the label). Reuse
   `lab.reset/check_baseline`, `injector.apply/revert`, `ground_truth.store`.
2. Per step: timeout from `workloads.yaml`, failure => `execution_status: error`
   and a run status (`invalid_baseline`, `injection_failed`, `dirty`, `error`);
   no silent retries (§8.2).
3. Ordering that tests must prove: label written after verified injection and
   before `investigate(...)`; `ground_truth_label` appears in the record only
   after the diagnosis is frozen (§8.1 steps 6–9).
4. Run record: build the §11 shape now (write it to `experiments/runs/<run_id>.json`);
   the formal JSON schema is §18 step 9.
5. One fault per run, one run at a time, `down -v` between runs; release the
   stack on every exit path.
6. Tests: fake docker CLI + local HTTP stub (see `backend/twin/tests/conftest.py`),
   stub diagnostic, assert `lab_status`, label timing, and teardown on failure.

## Later phases (§18)

| Phase | Steps | Scope |
|---|---|---|
| 4 | 6–7 | Call `backend.api.investigate`, then wrap existing `backend/collectors/` into `available_tests`/`execute_test`/`TestResult` |
| 5 | 8 | `worker_process_stopped`, `redis_network_unreachable`, `connectivity_or_endpoint`, unmodeled faults for `other_unknown` |
| 6 | 9–10 | `experiments/schema/run_record.schema.json`, per-fault aggregates, calibration/evaluation splits |
| 7 | 11 | Repair validator (`catalogues/repairs.yaml`) |
| 8 | 12 | Energy measurement after latency is reliable |
| 9 | 13 | Twin self-validation, results in `experiments/validation/` |

## Findings to carry forward

- **Worker cascade:** stopping `redis` or `db` makes the worker exit(1)
  (`worker/Program.cs` catches and returns). `worker/restart: unless-stopped` is
  now in the override, so a fault no longer kills the worker permanently. Manual
  `docker stop worker` (a future fault) is still respected.
- **`connectivity_or_endpoint` cannot be env-injected:** the worker hardcodes
  `Server=db` and `redis`; it needs DNS/`/etc/hosts` or network-level injection.
- **Override needs Compose ≥ 2.24:** `!override` replaces the base port list;
  Compose appends otherwise, exposing the lab on `0.0.0.0`.
- **Vote verification is the DB row, not the UI:** `result` renders counts over
  socket.io, so baseline polls `votes` in database `postgres` (table `votes`).
- **Do not run Docker as the agent's default user:** this session needs
  `sg docker -c '...'` until opencode is restarted after `usermod -aG docker`.
- **Storage cap:** 40 GiB at `/var/lib/docker`; containerd's root points at
  `/var/lib/docker/containerd` so images and build cache are inside the cap.
  `docker system df` before large builds; prune build cache if it fills.

## Out of scope until later

`frontend/`, upstream service source, and `backend/twin/repair/` (Phase 7).
