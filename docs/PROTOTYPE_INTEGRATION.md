# Integrated prototype — 15 November 2026

## Scope

This integrates `origin/feat/twin-phase-2` at `11988a1` into the collector branch
at `d862e89`, including a snapshot of the current uncommitted launcher/readiness
work. Work is isolated on `integration/prototype`; the original checkout and
its uncommitted files are preserved.

This is a research prototype that selects diagnostics, collects evidence, updates
beliefs and abstains. It is not validated automatic root-cause diagnosis.

## Merge choices

- Retain the collector branch's catalogue definitions and runtime verification.
  Static `verified` flags from the twin branch are not evidence of verification.
- Retain the current demo Compose file, pinned images, ownership manifest,
  launcher, restoration harness and recorded readiness evidence.
- Preserve the twin's vendored base as `lab/compose.twin.yaml`; update its runner,
  override, target lock and instructions. Its fixed `infrasage-lab` lifecycle is
  distinct from the demo lab. Netdata is an optional `monitoring` profile.
- Retain the twin runner, ground truth store, repair validation, split assignment,
  run schema, scoring, and optional energy accounting.
- Use one live diagnostic pipeline with Redis runtime, worker runtime and
  worker-to-Redis TCP checks. Worker runtime is opt-in at the core configuration
  boundary for compatibility and enabled by the CLI and twin API. Their default
  attempt budget is three. Confidence thresholds are unchanged.

## Integration fixes

- The public Python API resolves unique project/service containers, including
  stopped containers, and verifies local helper provenance before using its
  immutable image ID. It closes clients and degrades to runtime checks if TCP
  prerequisites are absent. It does not pull images during investigation.
- Native runtime outcomes remain `running`, `stopped`, and `restarting`; they are
  no longer relabeled as TCP results. Target identity is recorded separately from
  an optional raw-artifact reference.
- Worker runtime uses the runtime deadline and shared adapter/provenance checks.
- Optional energy telemetry executes a diagnostic once and cannot discard valid
  evidence when telemetry fails. The shared RAPL server reads actual pyJoules
  traces and sums only package domains. CPU fallback uses the diagnostic interval,
  not the duration of the stop HTTP request, and remains explicitly estimated.
- The run schema accepts optional baseline energy and incomplete failure records.
  The runner honors caller target, seed and warmup duration, counts successful
  submissions, checks revert status and verifies teardown residue.
- Calibration aggregation excludes execution errors, invalid/unverified runs,
  evaluation runs and unknown-cause runs. Smoothing covers each test's complete
  catalogue vocabulary. This does not make adaptive diagnostic traces an unbiased
  calibration protocol; generated tables are not automatically installed as the
  diagnostic model.
- Placeholder symptom/isolation checks report unverified rather than passing.
  Reproducibility rejects failed runs; reset verification starts a fresh lab before
  checking its baseline. Historical status claims are labeled as historical.
- CI runs both unit-test suites with Python 3.12 without starting Docker stacks.

## Validation

**Final result: 436 tests passed in 116.10 seconds**, including 31 integration
regression cases. Catalogue validation, the synthetic mathematical example,
Python compilation, shell syntax checks and twin Compose configuration validation
also passed. No live Docker faults were injected and no hardware RAPL readings
were taken.

Commands:

```bash
.venv/bin/python -m pytest -q tests backend/twin/tests
.venv/bin/python scripts/validate_catalogues.py
.venv/bin/python -m backend.diagnosis.example
docker compose -f lab/compose.twin.yaml -f lab/docker-compose.infrasage.yml config -q
```

The twin tests use fake Docker operations and localhost HTTP fixtures. The sandbox
blocks localhost sockets, so the combined test run requires permission to open
those fixture sockets. These tests do not inject faults into running Docker labs.

## Use

From the integration worktree:

```bash
./infrasage.sh setup
./infrasage.sh start
./infrasage.sh investigate
```

The demo manifest is intentionally not copied from the original checkout: it
contains local resource identities. `start` creates a separate owned demo lab.
To avoid creating an additional lab, retain the original checkout for existing
lab operations until deliberately switching to the integrated version.

For disposable twin experiments, follow `lab/README.md` and prepare the helper:

```bash
.venv/bin/python scripts/build_tcp_probe_image.py
docker compose -p infrasage-lab -f lab/compose.twin.yaml \
  -f lab/docker-compose.infrasage.yml build
```

The Python runner `backend.twin.runner.experiment.run(...)` owns twin reset,
injection and teardown. Run it only for the dedicated twin project. Do not layer
the twin override onto `lab/compose.yaml`.

## Remaining limits

- No new live Docker fault experiment, repair experiment, or hardware RAPL reading
  has been validated for this merged version. Historical runs are not new results.
- Priors, likelihoods and planner costs remain provisional. The healthy state,
  network-unreachable outcomes and richer live probes remain future work.
- The twin workload currently covers warmup, rather than continuous traffic
  through investigation. Full symptom-coupling and runtime network/log isolation
  validation remain unverified.
- Some legacy DNS/network injection and repair scenarios assume the twin's fixed
  network layout. They require fresh verification before reporting results.
- Energy readings represent host package intervals; fallback estimates are rough.
  Neither establishes attributable per-probe energy or energy savings.
- Complete raw-evidence artifact persistence, fixed-core calibration runs and
  independently held-out diagnosis evaluation remain necessary research work.
- HTTP serving and the InfraSage dashboard remain placeholders. The public Python
  API is functional and separate from an HTTP endpoint.
