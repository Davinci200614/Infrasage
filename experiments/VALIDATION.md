# InfraSage validation — 2026-11-12

**Conclusion: ready for collector integration as a read-only research prototype.** The finite-model mathematics, observation contracts, and bounded termination pass the checks below. This is not evidence of reliable operational diagnoses: uncalibrated likelihoods, correlated evidence, and unrecognized faults can produce confidently incorrect results. Collector integration should gather and label evidence for calibration; it should not trigger automatic remediation.

## Executed checks and corrections

- Python 3.12.3; pytest 9.2.0. Initial suite: **33 passed**. Expanded suite: **63 passed**; exhaustive checks added: **65 passed**. Final run: **65 passed in 3.33 s**. No failing tests remain.
- Independent references: Fraction arithmetic for three-hypothesis posteriors and sequential likelihood products; 60 seeded rational models checked against the direct mutual-information identity `sum_h,o p(h)L(o|h) log2(L(o|h)/P(o))`; analytic entropy, impossible evidence, zero priors, and confidence-threshold boundaries. References do not call engine entropy or Bayes functions.
- Exhaustively enumerated all 32 complete potential outcome vectors at both energy budgets for all four policies. Exact rational joint masses verify every terminal posterior given only the executed evidence, and exact integrated top-label reliability agrees with confidence (absolute tolerance 2e-14). This establishes matched-model correctness, not calibration on real infrastructure.
- **Fixed:** stopping selected a lexicographic leader while result construction used insertion order. At confidence .5 this could return `DIAGNOSED other_unknown`. Result construction now uses the same tie rule.
- **Fixed:** a free informative test produced an infinite IG/joule score, breaking strict JSON. Selection now uses `score=None, zero_cost_priority=True`, retaining deterministic priority and JSON compatibility.
- **Experimental defect corrected:** the old comparison generated truth from the inference model itself. Experiments now contain separate true priors, true likelihoods, observation corruption/dependence, and assumed inference parameters. The true model is never supplied to the policy or inference engine.
- No defect was found in the core Bayes, entropy, or expected-information-gain formulas within the finite-model tests. No architecture rewrite; collector, frontend, and twin code were not changed.

## Paired experiments

Executed **56,000 investigations**: seven scenario datasets, 1,000 incidents each, two predicted-energy budgets (100 J and 6 J), and four policies. Seed 42; confidence .95; at most four attempts; at least one successful observation. Within each scenario, all policies and budgets consume the same pre-sampled potential outcomes. SHA256 fingerprints, complete true/assumed models, class counts, termination reasons, and all reliability bins are saved in [validation_results.json](validation_results.json). Identities for all 56 result rows were audited. Exact repeated runs of matched/100 J and noisy/6 J are recorded in the command log.

Every cost below is **synthetic estimated cost, not measured energy or latency**. Overall accuracy = correct known-cause diagnoses / all incidents. Diagnosed accuracy = correct / diagnosed incidents. Abstentions are never counted as correct diagnoses. Energy/correct uses energy from **all** incidents in the numerator, including abstentions, divided by correctly diagnosed incidents. Undefined ratios are null (`n/a` in CLI output).

### Matched model: policy and budget comparison

Each row has 1,000 incidents. Joules and seconds are totals across those incidents.

| Budget J | Policy | Overall accuracy | Diagnosed accuracy | Abstain | Mean tests | Total J | J/correct | Total seconds | Brier |
|---:|---|---:|---:|---:|---:|---:|---:|---:|---:|
| 100 | fixed | 86.8% | 96.3% | 9.9% | 3.126 | 7512 | 8.654 | 1152.0 | 0.080 |
| 100 | information_gain | 79.4% | 99.9% | 20.5% | 2.410 | 14709 | 18.525 | 2400.8 | 0.047 |
| 100 | ratio | 86.8% | 96.3% | 9.9% | 3.115 | 7479 | 8.616 | 1146.5 | 0.080 |
| 100 | weighted | 79.4% | 99.9% | 20.5% | 2.410 | 14709 | 18.525 | 2400.8 | 0.047 |
| 6 | fixed | 84.1% | 96.2% | 12.6% | 3.000 | 6000 | 7.134 | 900.0 | 0.133 |
| 6 | information_gain | 84.1% | 96.2% | 12.6% | 3.000 | 6000 | 7.134 | 900.0 | 0.133 |
| 6 | ratio | 84.1% | 96.2% | 12.6% | 3.000 | 6000 | 7.134 | 900.0 | 0.133 |
| 6 | weighted | 84.1% | 96.2% | 12.6% | 3.000 | 6000 | 7.134 | 900.0 | 0.133 |

IG selects the expensive workflow test early; fewer tests therefore need not mean lower energy or latency. Its higher diagnosed accuracy accompanies more abstention and lower overall incident accuracy. The configured weighted policy (0.02 bits/J, 0.01 bits/s) matches IG on this dataset; this is a parameter-specific result. The 6 J budget excludes the 12 J workflow test. Its equal aggregate metrics do not imply identical test orders. These are descriptive simulations, not proofs of policy superiority or global energy optimality.

### Sensitivity to model assumptions

IG/joule policy, 100 J budget. Full results for every policy and both budgets are in the JSON artifact. Confidence below is the mean posterior maximum **among diagnosed cases**; Brier and ECE use **all incidents**.

| Scenario | Overall accuracy | Diagnosed accuracy | Abstain | Diagnosed confidence | Brier | ECE | Total J | Total seconds |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| matched | 86.8% | 96.3% | 9.9% | 96.1% | 0.080 | 0.004 | 7479 | 1146.5 |
| wrong_prior | 79.7% | 96.0% | 17.0% | 99.2% | 0.104 | 0.040 | 10681 | 1693.1 |
| zero_prior | 49.8% | 96.5% | 48.4% | 97.3% | 0.731 | 0.335 | 13917 | 2257.3 |
| likelihood_mismatch | 66.8% | 85.8% | 22.1% | 96.1% | 0.350 | 0.126 | 10320 | 1620.0 |
| noisy | 61.2% | 81.5% | 24.9% | 96.1% | 0.424 | 0.158 | 11376 | 1796.0 |
| correlated | 85.6% | 99.4% | 13.9% | 96.1% | 0.095 | 0.011 | 7722 | 1187.0 |
| out_of_catalogue | 69.9% | 76.2% | 8.3% | 96.1% | 0.429 | 0.189 | 7374 | 1129.0 |

- Wrong prior: assumed worker probability .85 and each other cause .05; true frequencies unchanged. At 6 J, ratio overall accuracy falls to 35.6% and abstention rises to 61.2%, compared with 84.1% and 12.6% under matched priors.
- Zero prior: worker probability is set to zero and remaining assumed probabilities rescaled; worker remains possible in truth. Bayes cannot recover a hypothesis assigned zero prior. High diagnosed accuracy hides these systematically missed incidents; all-incident Brier exposes them.
- Imperfect likelihoods: true outcome rows are .7 times the assumed row plus .3 times a uniform row. Noisy observations: after true-model sampling, 20% are replaced by a uniformly chosen different valid outcome. Neither intervention is disclosed to inference. Both degrade calibration and accuracy.
- Correlation: every test shares one uniform inverse-CDF quantile per incident, preserving each conditional marginal while violating conditional independence. Effects depend on policy; they do not invariably worsen every metric. Independent analytic duplicate-evidence test: true posterior .8 versus incorrectly independent posterior 16/17. Distinct test IDs alone cannot prevent this.
- Out-of-catalogue: 20% novel network-partition mass; other true cause frequencies scaled by .8. The novel cause has exactly the Redis outcome distribution, so these tests cannot distinguish it. Of 205 novel incidents, ratio falsely diagnosed 196 (95.6%); IG falsely diagnosed 173 (84.4%). Adding `other_unknown` is not an automatic novelty detector.

### Calibration and reliability

Brier = mean `sum_h (p_h - one_hot_h)^2`, unscaled range [0,2], lower is better. For evaluation only, external causes map to `other_unknown`; this evaluates aggregate novelty probability, not identification of the novel cause. Reliability uses the deterministic top label on every terminal posterior, including abstentions and unknown leaders. Ten equal-width bins are [lo,hi), with 1 included in the last bin. ECE is the count-weighted absolute bin gap; it is bin-dependent and does not establish classwise calibration.

Nonempty bins for IG/joule at 100 J (empty bins remain explicit with null means in JSON):

| Scenario | Confidence bin | Count | Mean confidence | Observed top-label accuracy |
|---|---|---:|---:|---:|
| matched | 0.4–0.5 | 1 | 49.4% | 0.0% |
| matched | 0.7–0.8 | 39 | 75.0% | 74.4% |
| matched | 0.8–0.9 | 2 | 82.8% | 100.0% |
| matched | 0.9–1.0 | 958 | 96.3% | 96.6% |
| noisy | 0.4–0.5 | 28 | 46.5% | 32.1% |
| noisy | 0.7–0.8 | 110 | 75.4% | 67.3% |
| noisy | 0.8–0.9 | 48 | 84.1% | 45.8% |
| noisy | 0.9–1.0 | 814 | 96.2% | 80.6% |
| correlated | 0.8–0.9 | 21 | 86.4% | 42.9% |
| correlated | 0.9–1.0 | 979 | 96.4% | 96.2% |
| out_of_catalogue | 0.4–0.5 | 2 | 49.4% | 0.0% |
| out_of_catalogue | 0.7–0.8 | 31 | 75.6% | 61.3% |
| out_of_catalogue | 0.8–0.9 | 7 | 82.8% | 85.7% |
| out_of_catalogue | 0.9–1.0 | 960 | 96.2% | 77.2% |

The noisy high-confidence bin predicts about 96% confidence but achieves about 81% accuracy. The correlated model has a small aggregate ECE while its 21 cases in the .8–.9 bin are substantially overconfident; small bins require caution. Matched-model empirical calibration is consistent with the exact enumeration, but one finite sample is not an empirical calibration certificate.

## Termination and remaining limitations

- All 56,000 investigations ended in DIAGNOSED or ABSTAIN, never CONTINUE; no duplicate test attempts, test-count overruns, or predicted-budget overruns were observed. Matched ratio at 100 J: 901 known diagnoses, 49 confident-unknown abstentions, 50 test-limit abstentions. Unit tests also cover no usable tests, insufficient information, failed/unavailable execution, exact affordability, and no-observation confidence handling.
- Budgets and reported costs are predicted; optional measured energy remains metadata. There is no hardware metering, collector execution, live orchestration test, or evidence of real-world energy savings.
- Confidence thresholds are heuristics. Real likelihood calibration, nonzero-support priors, correlation/dependency handling and novelty assessment remain necessary before relying on diagnoses. Multiple initiating faults, dependent likelihood learning, and long-horizon optimization remain outside scope.
- Experiments use one main seed and one severity per stress scenario, with exact repeat checks and smaller deterministic test seeds. Finite-sample policy differences are descriptive, not significance or generalization claims. Pairing is guaranteed within each scenario and across its budgets; not all stress scenarios share the same incident draws. Reliability bins may be sparse.
- Dataclass dictionaries remain mutable; concurrent model mutation is unsupported. Typed Python contracts are not a substitute for API-boundary schema validation. No changes were made to collector or digital-twin interfaces. Free informative ratio selections now expose a nullable score plus an explicit priority flag; downstream serializers should honor that contract.

## Commands and evidence

All shell inspection/validation commands and inline check bodies are recorded in [VALIDATION_COMMANDS.md](VALIDATION_COMMANDS.md). Primary commands:

```bash
.venv/bin/python -m pytest -q
.venv/bin/python -m experiments.compare_policies --suite --incidents 1000 --seed 42 --budgets 100 6 --output experiments/validation_results.json > /tmp/infrasage-validation-table.txt
.venv/bin/python -m backend.diagnosis.example > /tmp/infrasage-example-validated.json
git diff --check
```

Core changes: `backend/core/models.py`, `backend/core/orchestrator.py`, and `backend/optimization/planner.py`. Experiment changes: `experiments/compare_policies.py`. Independent tests: `tests/test_validation.py`; the original `tests/test_engine.py` remains intact. No dependency additions. This report and JSON results are the validation artifacts.
