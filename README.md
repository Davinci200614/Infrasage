# InfraSage

A Bayesian troubleshooting system for Linux/Docker infrastructure. It gathers
evidence with cheap probes, updates a posterior over causes, and abstains when
the evidence does not justify a recommendation.

## Status

The diagnostic backend is implemented through the investigation entry point
(`backend/api/investigate.py`). The digital twin — the Docker lab that injects
known faults to calibrate and evaluate the engine — is complete through step 13
of its build order (lab manager, workload, injector, ground truth, experiment
runner, real diagnostic integration, remaining faults, run records/scoring,
calibration/eval split, repair validator, energy measurement, twin self-validation).

- Phase tracker: `backend/twin/STATUS.md`
- Normative brief: `backend/twin/DIGITAL_TWIN_DEVELOPMENT_GUIDE.md`
- Lab stack and host setup: `lab/README.md`

## Layout

| Path | Contents |
|---|---|
| `backend/api/` | Public investigation entry point (the twin calls only this) |
| `backend/collectors/` | Evidence probes, reused by the twin |
| `backend/core/` | Diagnostic types and orchestration |
| `backend/diagnosis/` | Bayesian engine, likelihoods, planner |
| `backend/optimization/` | Test-selection policy |
| `backend/energy/` | Energy measurement/estimation helpers (shared) |
| `backend/twin/` | Digital twin subsystem (tracker, catalogues, runner, injector, ground truth, validation) |
| `lab/` | Pinned Voting App stack: the system under test |
| `experiments/` | Run records, aggregates, validation output — data only |
| `frontend/` | UI, out of scope for the twin |

## Quick start

```sh
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/python -m pytest backend/twin/tests -q     # 32 twin self-tests
```

Lab stack (requires the `docker` group; in a shell older than that change, wrap
commands with `sg docker -c '<command>'`):

```sh
docker compose -p infrasage-lab -f lab/compose.yaml -f lab/docker-compose.infrasage.yml up -d --wait
docker compose -p infrasage-lab -f lab/compose.yaml -f lab/docker-compose.infrasage.yml down -v
```

Run one experiment:

```sh
.venv/bin/python -c "
from backend.twin.runner import experiment
record = experiment.run('redis_process_stopped', seed=420, run_id='my-run-001', measure_energy=True)
print('Status:', record['lab_status'])
print('Decision:', record['diagnosis']['decision'])
print('Ground truth:', record['ground_truth_label'])
"
```

## Acceptance criteria (V0.1)

- [x] Target pinned by commit and image digest (`lab/target.lock`)
- [x] Single command runs end-to-end, writes valid run record
- [x] All modeled faults + `other_unknown` have verified injection
- [x] Ground-truth isolation test passes
- [x] Reset leaves no residue (tested 5 cycles, 20 target)
- [x] All run records validate against schema
- [x] Calibration/evaluation splits assigned by seed
- [x] Energy values carry explicit `energy_method`
- [x] Twin self-validation results stored in `experiments/validation/`
- [x] No Docker socket exposure beyond dedicated lab host
- [x] `.env` and credentials excluded by `.gitignore`