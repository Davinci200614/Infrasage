# InfraSage

A Bayesian troubleshooting system for Linux/Docker infrastructure. It gathers
evidence with cheap probes, updates a posterior over causes, and abstains when
the evidence does not justify a recommendation.

## Status

The diagnostic engine is still stubs. The digital twin — the Docker lab that
injects known faults to calibrate and evaluate the engine — is implemented
through step 4 of its build order (lab manager, workload generator, fault
injector, ground-truth store), verified against the real stack.

- Phase tracker and next steps: `backend/twin/STATUS.md`
- Normative brief: `backend/twin/DIGITAL_TWIN_DEVELOPMENT_GUIDE.md`
- Lab stack and host setup: `lab/README.md`

An earlier 3-member ownership map (`git show a0f5de1:README.md`) is superseded
by the guide's §17.1 layout.

## Layout

| Path | Contents |
|---|---|
| `backend/api/` | Public investigation entry point (the twin calls only this) |
| `backend/collectors/` | Evidence probes, reused by the twin |
| `backend/core/` | Diagnostic types and orchestration |
| `backend/diagnosis/` | Bayesian engine, likelihoods, planner |
| `backend/optimization/` | Test-selection policy |
| `backend/energy/` | Energy measurement/estimation helpers (shared) |
| `backend/twin/` | Digital twin subsystem (tracker, catalogues, runner, injector, ground truth) |
| `lab/` | Pinned Voting App stack: the system under test |
| `experiments/` | Run records, aggregates, validation output — data only |
| `frontend/` | UI, out of scope for the twin |

## Quick start

```sh
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/python -m pytest backend/twin/tests -q     # 19 twin self-tests
```

Lab stack (requires the `docker` group; in a shell older than that change, wrap
commands with `sg docker -c '<command>'`):

```sh
docker compose -p infrasage-lab -f lab/compose.yaml -f lab/docker-compose.infrasage.yml up -d --wait
docker compose -p infrasage-lab -f lab/compose.yaml -f lab/docker-compose.infrasage.yml down -v
```
