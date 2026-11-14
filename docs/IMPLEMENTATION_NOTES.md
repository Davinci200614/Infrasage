# InfraSage V0.1 — implementation and catalogue source strategy

## 1. Precise responsibilities

| Component | Contract | Does not do |
|---|---|---|
| Netdata Agent | Continuous metrics, alerts, and available Functions | Bayesian decisions |
| NetdataCollector | `collect()` retrieves raw snapshot / `execute_test()` for verified supported operations | Root-cause inference |
| DockerCollector / precision | Container metadata and narrowly scoped tests not supplied by Netdata | Autonomous decision-making |
| CollectorManager | `collect_snapshot(target)` + `execute_test(test, target)`; provider routing and errors | Choice of next test |
| Diagnostic Adapter | Apply `tests.yaml` normalization mappings, timestamps, target, statuses | Invent causality or Bayesian weights |
| InvestigationOrchestrator | Investigation lifecycle, initial evidence, planner dispatch, stopping rules, optional twin | Math of Bayes or Netdata-specific calls |
| Knowledge catalogue | Hypothesis definitions, qualitative outcome assessments, relevance | Executable API details / probability weights |
| Test catalogue | Operations, provider, outcomes, prerequisites, energy-cost metadata | Test order |
| Probability model | Prior/conditional outcome probabilities | Collector execution |
| Planner | Consider eligible tests; compute IG/cost and stop decision | Execute test |
| Digital twin | Produce known-fault experiment data, later controlled hypothesis/repair experiments | Guarantee real root cause |

## 2. How a test is chosen

1. **Incident classification:** select the hypothesis set from `knowledge.yaml`; do not assume all 15 are plausible in every situation.
2. **Available evidence:** CollectorManager obtains a batch; adapter maps raw measurements into exact registered `(test_id, outcome, target, observed_at)` records. Only new, valid, modelled outcomes update the prior.
3. **Candidate test set:** union relevant `relationship.test_id`s for plausible hypotheses, then filter based on installed capability, target, prior consumption, privacy/safety, freshness and prerequisites. For unknown/other causes consider conservative additional checks/abstention.
4. **Model prediction:** for each remaining eligible test, read `P(outcome|hypothesis,test)` from `probabilities.json`; calculate expected information gain under current posterior.
5. **Cost:** read measured estimated incremental joules and latency from a later cost model. In the bootstrap, test costs are *illustrative only*.
6. **Decision:** planner returns `test_id` or `diagnose`/`abstain`; orchestrator resolves the executable provider operation through `tests.yaml` and CollectorManager, normalizes output, updates posterior, then repeats.
7. **Twin:** experiments can collect calibration labels offline; online twin comparison is optional, costly, and must not update the posterior until comparison outcome likelihoods are calibrated conditionally on previous evidence.

No YAML file encodes a predetermined execution order.

## 3. Source usage and what it does **not** prove

**Voting App topology** (https://github.com/dockersamples/example-voting-app): Five services and their dependencies—vote -> Redis -> worker -> PostgreSQL -> result. Used to form candidate causes and targeted tests; not a provider of root-cause probabilities.

**Netdata** (https://learn.netdata.cloud/docs/live-view): documents monitoring and on-demand Functions. The exact functions available to an unauthenticated local Agent differ; some functions require Cloud authentication. This package assigns logical operation IDs only. A live discovery and permission check must resolve real endpoints and parameters.

**Awesome Prometheus Alerts** (https://github.com/samber/awesome-prometheus-alerts): a source of Linux, Docker, Redis, PostgreSQL and HTTP monitoring conditions. The upstream `_data/rules.yml` is the source file but not itself a Prometheus config. Use it as **candidate vocabulary**; alert conditions are not verified initiating causes, and PromQL expressions don't automatically execute on Netdata. No individual rules have been imported verbatim into this bundle. Track upstream commit and license when doing future rule imports.

## 4. Suggested schema contract

Logical diagnostic request:

```json
{"investigation_id":"inv-001","request_id":"req-001","test_id":"redis_runtime_state","target":{"node_id":"node-01","component":"redis"},"timeout_ms":5000}
```

Normalized result:

```json
{"investigation_id":"inv-001","request_id":"req-001","test_id":"redis_runtime_state","status":"COMPLETED","outcome":"stopped","source":"docker","observed_at":"2026-11-14T10:00:00Z","retrieved_at":"2026-11-14T10:00:01Z","raw_evidence_ref":"obs-001"}
```

`FAILED` or `UNAVAILABLE` results must have `outcome=null` unless a separate diagnostic outcome for that failure has been explicitly defined and validated. Keep the acquisition source and incident environment visible.

## 5. What to validate before live use

- Discover exact Netdata contexts, alerts and available Functions, including authorization.
- Prove every enabled provider binding by executing a safe sample against the Voting App.
- Configure the Redis/PostgreSQL integrations and determine precise result-to-outcome mappings.
- Verify that `redis_runtime_state` from Docker/Netdata is not counted twice under separate aliases.
- Instrument `energy/meter.py` and replace synthetic costs with repeated measurements.
- Run independent labeled fault-injection experiments; estimate likelihoods, attach counts and uncertainty, use held-out evaluation.
- Establish representative priors or treat initial priors as context-specific provisional assumptions.
- Handle unknown causes, correlated metrics, time dependence and multi-fault cases; use abstention where calibration is insufficient.

## 6. Recommended next vertical slice

Use only 3 hypotheses (`redis_process_stopped`, `worker_redis_network_failure`, `other_unknown`) and two tests (`redis_runtime_state`, `worker_redis_connectivity`) from these larger files. Extract a complete submodel programmatically (renormalize priors over selected hypotheses; do not claim those renormalized priors reflect real incident frequency). Then wire one real provider observation into your existing Bayesian engine and planner. Expand when that passes.
