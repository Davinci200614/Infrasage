# InfraSage mathematical engine

A compact, infrastructure-independent Bayesian diagnostic prototype for Python 3.12.
No Docker daemon, frontend, database, hardware meter, or network is required at runtime.
Collector and digital-twin modules remain untouched.

## Run

From the repository root:

```bash
python -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python -m pytest -q
.venv/bin/python -m backend.diagnosis.example
.venv/bin/python -m experiments.compare_policies --incidents 1000 --seed 42
```

The example prints a structured investigation as JSON. Dataclasses can be converted
with `dataclasses.asdict`; serialize timestamps as ISO strings. Ratio selection uses
an infinite internal score for informative zero-energy tests; convert this to an
explicit string/null plus a free-test flag before strict JSON/API serialization.
The default example uses finite weighted scores.

## Model and equations

The model is M=(H,T,O,b0,L,CE,CL,policy). Hypotheses are mutually exclusive and
exhaustive **single initiating causes**, including `other_unknown`. This does not
model simultaneous independent failures. Every prior and conditional outcome row
must be finite, nonnegative, and sum to one within absolute tolerance 1e-9. Invalid
inputs are rejected rather than arbitrarily renormalized.

- `backend/core/models.py`: typed hypotheses, tests, observations, beliefs,
  selections and results; validation of distributions and estimated costs.
- `backend/diagnosis/bayesian.py::bayesian_update`: computes
  b'(h)=L(o,h)b(h)/sum_j L(o,j)b(j). Log weights and a shifted exponential avoid
  underflow during normalization. Unsupported and impossible observations raise
  `ValueError`. The prior is never mutated.
- `entropy`: H(b)=-sum_h b(h)log2(b(h)), with zero terms omitted; units are bits.
- `expected_information_gain`: predicts P(o)=sum_h L(o,h)b(h), enumerates every
  supported outcome, and computes H(b)-sum_o P(o)H(b' given o). Zero-probability
  outcomes are skipped. Negative artifacts up to 1e-9 bits are clamped; larger
  negatives raise `ArithmeticError`.
- `backend/optimization/planner.py::DiagnosticPolicy.select`: fixed order,
  maximum information gain, gain/joule (`ratio`), or weighted gain minus
  lambda_energy*joules minus lambda_latency*seconds (`weighted`). Coefficients
  have units bits/joule and bits/second. Informative free tests have ratio priority;
  uninformative free tests score zero. Equal scores break by test identifier.
  The selection contains the test, policy score, IG and both estimated costs.
- `backend/core/orchestrator.py::stopping_decision`: confidence heuristic, minimum
  successful observations, attempt limit, energy affordability, remaining tests,
  and insufficient information. Known causes reaching confidence are diagnosed;
  a confident unknown cause abstains. An uncertain unknown leader can continue
  until a constraint is reached. Confidence is not a guarantee of accuracy.
- `run_investigation`: scores affordable remaining tests, samples or consumes an
  observation, updates beliefs, and records snapshots and costs. Every test is
  attempted at most once, including failures. Duplicate identifiers are rejected.

Sequential observations are assumed conditionally independent given the initiating
hypothesis and selected test. Correlated evidence can cause overconfidence even
when tests have different identifiers. Do not re-feed identical observations as
independent evidence; the runner enforces one attempt per test per investigation.
Models contain mutable dictionaries for convenient integration: callers should
not mutate them during an investigation. Inference revalidates likelihood tables.

The runner uses a **predicted** energy budget. It checks affordability before each
attempt and charges the full estimated cost of all attempts, including failed or
unavailable executions. Optional measured energy is retained as metadata but does
not alter this predicted-budget accounting. Latency totals also represent estimates,
not actual durations. An exact budget boundary permits zero-cost tests. A diagnosis
obtained on the last affordable test can still be returned.

## Synthetic example and comparison

`backend/diagnosis/example.py::synthetic_model` returns priors and four Voting App
inspired tests, including a four-outcome application workflow test. Every probability,
energy and latency value is a **hypothetical illustrative assumption**, not calibrated
or measured data. `other_unknown` is part of every likelihood table.

The comparison samples the same initiating causes and a complete set of potential
test outcomes for all policies with a local seeded RNG. This preserves paired
scenarios even if policies select different tests. The synthetic runner also uses
deterministic synthetic timestamps. Predefined observation mode consumes the supplied
observation for each selected test and raises if it is missing.

The table reports overall correct known-cause diagnoses / all incidents, correct /
diagnosed incidents separately, abstention / all incidents, mean tests, estimated
joules, estimated seconds, and termination entropy. Energy per correct diagnosis
is **sum of estimated energy over all incidents (including failures and abstentions)
divided by count of correctly diagnosed incidents**; a zero denominator is `n/a`.
Correct unknown abstentions are counted as abstentions, not correct diagnoses.
These simulations validate implementation and explore policies; they demonstrate
neither measured energy savings nor real-world diagnostic performance. Greedy
policies do not establish globally minimum total investigation energy.

## Collector integration contract

A collector adapter should implement this Python interface (no shared collector
file needs modifying for the engine):

```python
from typing import Protocol
from backend.core.models import DiagnosticTest, Observation

class ObservationCollector(Protocol):
    def execute(self, test: DiagnosticTest, target_identifier: str) -> Observation: ...
```

Map `test.identifier` to collector-specific commands **outside** the engine. Return:

- `test_identifier` exactly matching the requested test and the requested target.
- `execution_status=ExecutionStatus.COMPLETED` with a supported symbolic
  `observed_outcome`, including valid unhealthy outcomes like `stopped`.
- `ExecutionStatus.FAILED` for an execution failure, or `UNAVAILABLE` for unsupported
  or unavailable execution, with `observed_outcome=None` in both cases.
- Nonnegative finite `duration_seconds`, optional nonnegative finite
  `measured_energy_joules`, and a timezone-aware `datetime` timestamp (UTC preferred).

Use `apply_observation(beliefs, test, observation)` to validate the test identity
and update only completed evidence. Failed/unavailable observations return a copy
of the unchanged prior. Validate target identity at the integration boundary, as
predefined runner mode does. Live orchestration should preserve the same attempt
tracking, affordability and stopping checks as the runner; the current runner is
intentionally simulation-only. For exact recorded scenarios, pass a mapping
`predefined_observations={test_id: observation}` to `run_investigation`, with the
matching `target_identifier`. Supply every test the policy may select. No ground
truth is required in predefined mode. Use `belief_history`, `observations`, and
`selections` for future dashboard explanations.

## Calibration and future work

Replace each hypothetical likelihood row with outcome frequencies estimated from
labeled incidents or controlled isolated experiments. Define outcome categories
before collecting data, retain unknown examples, smooth sparse counts, validate on
held-out incidents, and version the model and calibration dataset. Assess posterior
calibration and stopping thresholds separately from classification accuracy. Replace
estimated scalar energy/latency with calibrated predictions from the future energy
module; distinguish predictions from measurements in exported results.

Current limitations include finite discrete outcomes, a fixed model, no measurement
uncertainty, no automated retries, no correlation model, no live execution loop,
and heuristic stopping. Future work includes dependent observation models, Bayesian
networks, learned likelihoods with parameter uncertainty, calibration under drift,
explicit abstention losses and decision-theoretic stopping, and long-horizon belief
state optimization. Collector execution failures may themselves have operational
causes, but this prototype intentionally assigns them no root-cause likelihood.
