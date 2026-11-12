# Validation command log — 2026-11-12

Commands ran from `/home/davinci/infrasage`. File-writing heredocs/apply_patch calls
are represented by the resulting source files rather than duplicated in this log.
Repeated invocations are grouped. No network, Docker or hardware meter was used.

## Inspection

```bash
git status --short && cat backend/core/models.py backend/core/orchestrator.py backend/diagnosis/bayesian.py backend/optimization/planner.py experiments/compare_policies.py tests/test_engine.py
cat backend/diagnosis/example.py
git diff -- backend/core/models.py backend/core/orchestrator.py backend/optimization/planner.py && git diff --numstat && ps -eo pid,etime,args
ls -lh experiments/validation_results.json /tmp/infrasage-validation-table.txt
wc -c /tmp/infrasage-validation-table.txt
cat /tmp/infrasage-validation-table.txt
```

The intermediate `ls` ran while experiments were still running: the JSON did not
exist yet (exit 2), and the table was empty. The experiment later completed with
exit 0 and produced both outputs. The `ps` output was noisy and truncated; it was
not used as validation evidence.

Data skill instructions were read through `cat` (read-only, no external sources):
`skills/index/SKILL.md`, `skills/validate-data/SKILL.md`,
`shared/shared-skill-instructions.md`, `shared/analysis-quality.md`, all beneath
`/home/davinci/.codex/plugins/cache/openai-curated-remote/data-analytics/1.1.0-exp-homepage/`.

## Pre-correction defect reproduction

Executed together with the `cat backend/diagnosis/example.py` inspection:

```bash
.venv/bin/python - <<'PY'
from backend.core.orchestrator import run_investigation, StoppingConfig
from backend.optimization.planner import DiagnosticPolicy
from backend.core.models import DiagnosticTest
p={'other_unknown': .5, 'known': .5}
t=DiagnosticTest('t', ('yes','no'), {h:{'yes':.6,'no':.4} for h in p},0,0)
r=run_investigation(p,[t],DiagnosticPolicy(), 'known', stopping=StoppingConfig(confidence_threshold=.5, minimum_successful_observations=0))
print(r.decision, r.diagnosed_hypothesis, r.reason)
from dataclasses import asdict
import json
p={'a':.5,'b':.5}
t=DiagnosticTest('free',('yes','no'),{'a':{'yes':1,'no':0},'b':{'yes':0,'no':1}},0,0)
try:
    json.dumps(asdict(DiagnosticPolicy('ratio').select(p,[t])),allow_nan=False)
except ValueError as e:
    print('Strict JSON failure:',e)
PY
```

Before fixes: `DIAGNOSED other_unknown posterior confidence threshold reached`;
`Strict JSON failure: Out of range float values are not JSON compliant: inf`.
Regression checks now live in `test_tie_decision_and_result_use_same_leader` and
`test_free_test_selection_strict_json` in `tests/test_validation.py`.

## Tests, versions and experiments

```bash
.venv/bin/python -m pytest -q
```

Three invocations before the final check: initial **33 passed in 1.26s**;
expanded **63 passed in 2.52s**; exhaustive **65 passed in 3.55s**.
(The final invocation is the grouped command below; total four invocations.)

```bash
git diff --check && python --version && .venv/bin/python -m pytest --version
```

Exit 0; Python **3.12.3**, pytest **9.2.0**.

```bash
.venv/bin/python -m experiments.compare_policies --suite --incidents 1000 --seed 42 --budgets 100 6 --output experiments/validation_results.json > /tmp/infrasage-validation-table.txt
```

Exit 0; **56 rows**, **56,000 policy investigations**, full metrics in JSON.

```bash
.venv/bin/python -m backend.diagnosis.example > /tmp/infrasage-example-validated.json && .venv/bin/python -m pytest -q > /tmp/infrasage-final-tests.txt && git diff --check && cat /tmp/infrasage-final-tests.txt
```

Exit 0; example completed; **65 passed in 3.33s**; no whitespace errors.

## Independent result-identity audit

```bash
.venv/bin/python - <<'PY'
import json, math
from pathlib import Path
report = json.loads(Path('experiments/validation_results.json').read_text())
rows = report['results']
assert len(rows) == 56
for row in rows:
    n, d, c = row['incidents'], row['diagnosed'], row['correct']
    assert n == 1000 and 0 <= c <= d <= n
    assert math.isclose(row['overall_accuracy'], c/n)
    assert math.isclose(row['diagnosed_accuracy'], c/d)
    assert math.isclose(row['abstention_rate'], (n-d)/n)
    assert math.isclose(row['mean_energy_joules']*n, row['total_estimated_energy_joules'])
    assert math.isclose(row['mean_latency_seconds']*n, row['total_estimated_latency_seconds'])
    assert math.isclose(row['energy_per_correct'], row['total_estimated_energy_joules']/c)
    assert sum(row['termination_reasons'].values()) == n
    assert sum(b['count'] for b in row['reliability_bins']) == n
    assert sum(v['incidents'] for v in row['by_truth'].values()) == n
    assert math.isclose(row['mean_posterior_confidence'], sum(b['count']*(b['mean_confidence'] or 0) for b in row['reliability_bins'])/n)
    assert math.isclose(row['top_label_accuracy'], sum(b['count']*(b['observed_accuracy'] or 0) for b in row['reliability_bins'])/n)
for scenario in {r['scenario'] for r in rows}:
    assert len({r['incident_fingerprint'] for r in rows if r['scenario'] == scenario}) == 1
print('PASS: 56 rows, 56,000 policy investigations; all metric/count identities and within-scenario pairing fingerprints reconcile.')
for row in rows:
    if row['stopping']['max_energy_joules'] == 100 and row['policy'] in ('ratio', 'information_gain'):
        print(row['scenario'], row['policy'], 'diagnosed',row['diagnosed'], 'correct',row['correct'],
              'diagnosed_confidence',round(row['diagnosed_mean_confidence'],4),
              'OOD_false_diagnosis',row['outside_catalogue_false_diagnosis_rate'],
              'termination',row['termination_reasons'])
print('Reliability bins (ratio, 100 J), count / confidence / observed accuracy:')
for scenario in ('matched','noisy','correlated','out_of_catalogue'):
    row=next(r for r in rows if r['scenario']==scenario and r['policy']=='ratio' and r['stopping']['max_energy_joules']==100)
    print(scenario,[(b['lower'],b['count'],round(b['mean_confidence'],4),round(b['observed_accuracy'],4)) for b in row['reliability_bins'] if b['count']])
PY
```

Exit 0; every count/ratio reconciliation and within-scenario fingerprint check passed.
This arithmetic audit complements the independent hand-worked pytest expectations;
it does not independently prove aggregate Brier values from individual raw records.

## Exact repeat checks

```bash
.venv/bin/python - <<'PY'
import json
from pathlib import Path
from backend.core.orchestrator import StoppingConfig
from experiments.compare_policies import compare
saved=json.loads(Path('experiments/validation_results.json').read_text())['results']
for scenario,budget in [('matched',100),('noisy',6)]:
    repeated=compare(1000,42,scenario,StoppingConfig(max_energy_joules=budget))
    expected=[r for r in saved if r['scenario']==scenario and r['stopping']['max_energy_joules']==budget]
    assert repeated==expected
    print(f'PASS: exact repeat of all four policies for {scenario}, budget={budget}, n=1000',flush=True)
PY
```

Exit 0. Both checks passed: exact equality of all metrics, bins, counts, termination
reasons, metadata, and fingerprints for all four policies in matched/100 J and
noisy/6 J (8,000 repeated investigations). The full 56,000-investigation suite was
not rerun a second time.

## Report authoring and final inspection

The report tables were generated from the saved JSON (not manually transcribed):

```bash
.venv/bin/python /tmp/write_infrasage_report.py
```

The temporary authoring script only formats the JSON and writes `experiments/VALIDATION.md`;
no tests or simulations run in that script. The durable report and JSON retain the results.

Final report inspection command (exit 0):

```bash
git diff --check && wc -l experiments/VALIDATION.md experiments/VALIDATION_COMMANDS.md && sed -n '1,160p' experiments/VALIDATION.md && git status --short
```
