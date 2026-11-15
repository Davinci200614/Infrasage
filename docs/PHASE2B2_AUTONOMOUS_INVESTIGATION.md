# Phase 2B.2 — First autonomous research investigation

Implemented on the existing Phase 1/2A/2B.1 codebase, starting from HEAD `ba9cf80`. No branch switch, commit, push, merge, reset or remediation was performed.

## Outcome

InfraSage now runs this live path inside the existing investigation lifecycle module:

```text
Catalogue prior + explicit investigation targets/budgets
  → configuration-only eligibility checks
  → existing stopping_decision()
  → existing DiagnosticPolicy.select()
  → CollectorManager executes ONLY the selected diagnostic
  → existing DiagnosticAdapter validates its evidence
  → duplicate/group/freshness admission checks
  → existing apply_observation()
  → posterior and chronological trace
  → repeat or DIAGNOSED / ABSTAIN
```

Only `redis_runtime_state` and `worker_redis_connectivity` can be selected. All 15 catalogue hypotheses and their existing provisional priors/likelihoods are retained. No other catalogue diagnostics are activated. Probabilities and costs remain synthetic research parameters, not calibrated diagnosis confidence or measured energy.

Real Docker investigations demonstrated two autonomous selections and posterior updates for both a reachable Redis endpoint and an intentionally closed port. Both safely abstained at the two-attempt limit. These are actual provider executions, independently of deterministic test mocks.

## Changes and preservation

| File | Change |
|---|---|
| `backend/core/orchestrator.py` | Additive live configuration/result records and `run_live_investigation()` in the existing lifecycle module |
| `backend/core/collector_manager.py` | Pure capability checks, single-action investigation dispatch, bounded runtime verification, and propagation of an absolute deadline to the existing TCP probe path |
| `scripts/demo_live_investigation.py` | Explicit-target CLI exposing the existing planner and live lifecycle trace |
| `tests/test_live_investigation.py` | 37 focused live-loop, budget, evidence and compatibility cases |
| `docs/PHASE2B2_AUTONOMOUS_INVESTIGATION.md` | This execution contract and verification report |

The original contents of `core/orchestrator.py` remain an unchanged byte-for-byte prefix of the file. `run_investigation()`, `StoppingConfig` and `stopping_decision()` were neither rewritten nor refactored. Live and simulation/replay entry points share the same lifecycle owner/module and existing stopping implementation. There is no second investigation controller in the collection layer.

SHA256 checks confirmed preservation of the mathematical models, Bayesian engine, planner, catalogue loader, CPU normalizer, DiagnosticAdapter, Docker/Netdata providers, TCP helper implementation and all three catalogues. The existing Netdata whitespace edit and untracked collector audit document were preserved. No frontend, twin, dependency or synthetic probability edits were made.

## Interfaces

```python
@dataclass(frozen=True)
class LiveInvestigationConfig:
    redis_target: dict | None = None
    worker_target: dict | None = None
    probe_config: dict | None = None
    maximum_duration_seconds: float = 30.0
    runtime_deadline_seconds: float = 3.0

run_live_investigation(
    catalogues,
    manager,
    configuration: LiveInvestigationConfig,
    policy: DiagnosticPolicy | None = None,
    stopping: StoppingConfig | None = None,
    initial_evidence: Sequence = (),
) -> LiveInvestigationResult
```

The returned companion record contains:

- `result`: the existing protected `InvestigationResult` dataclass.
- `trace`: chronological event dictionaries.
- `evidence_records`: admitted existing NormalizedObservation envelopes, retaining raw execution provenance.
- `rejected_evidence`: explicit rejection reasons and available original evidence.
- `elapsed_seconds`: measured investigation duration.

The manager must reference the same LoadedCatalogues instance supplied to the investigation. Explicit research mode is required. The numerical test tables and prior are validated through existing model checks before diagnostic work begins.

The default live policy is the existing `DiagnosticPolicy('ratio')`. Other existing policies can be supplied; their scoring, tie-breaking and validation are unchanged. The initial prior is copied from the catalogue, not guessed or replaced by a fabricated incident-specific distribution.

### Explicit target resolution

```python
configuration = LiveInvestigationConfig(
    redis_target={'container_id': '<full Redis ID>'},
    worker_target={'container_id': '<full worker ID>'},
    probe_config={
        'redis_host': 'redis',
        'redis_port': 6379,
        'helper_image': 'sha256:<controlled local image ID>',
        'connection_timeout': 1.0,
        'execution_deadline': 8.0,
    },
    maximum_duration_seconds=30.0,
    runtime_deadline_seconds=3.0,
)
```

Exact container names are also supported by the existing provider contract. Runtime state routes to redis_target; worker connectivity routes to worker_target plus the explicit probe configuration. There is no service-name guessing, container-name search or automatic topology discovery.

## Eligibility versus verification

Additive manager interfaces:

```python
CollectorManager.check_test_capability(
    self, test, target, *, probe_config=None, deadline_seconds=None,
) -> dict

CollectorManager.execute_investigation_test(
    self, test, target, *, probe_config=None, deadline_seconds,
) -> dict
```

`check_test_capability()` is pure: it checks supported IDs/operations, explicit research configuration, valid target shape, helper/endpoint/budget configuration and required deadline. It performs no Docker calls, image pulls, runtime inspection or TCP attempt. Its result explicitly states `verification_performed=False`. It establishes a configured execution pathway, not that the worker, image or daemon is actually available. Those live conditions are checked only after selection; unavailable providers remain explicit unsuccessful attempts.

The orchestrator additionally filters attempted tests, consumed evidence groups and operations whose configured deadline does not fit the remaining investigation time. It exposes both configured eligible and energy-affordable sets in the trace. Existing stopping and planner functions compute information gain and affordability; no new information-gain or Bayesian formulas are implemented.

### Verification evidence is the one selected action

The prior phases' binding-verification methods already execute a real observation-producing operation:

- `verify_runtime_binding()` inspects Redis runtime state.
- `verify_connectivity_binding()` launches the worker-network TCP helper.

Therefore, after selection, `execute_investigation_test()` invokes exactly one of those methods and returns its raw evidence. It does **not** call `execute_test()` again after verification. Ranking never runs candidate diagnostics in advance.

Each selected action records `action_phase=binding_verification_with_evidence`, `diagnostic_actions=1` and `evidence_from_verification=True` when it reaches that path. The returned observation is normalized and admitted once. A configuration rejection can record zero provider actions, but still consumes the selected attempt conservatively. Provider setup failures are failed selected attempts. All unsuccessful attempts are charged the full provisional test estimate; no energy savings are inferred from early failure.

The process-local verified binding remains target/operation/endpoint/image-specific. Catalogue files remain disabled and unchanged. This loop needs no persistent live binding or blanket activation of the other 18 tests.

## Evidence correctness and initial evidence

Every selected result passes through the unchanged `DiagnosticAdapter.adapt_with_metadata()`. The lifecycle then rechecks the actual test ID, canonical target, outcome vocabulary, execution status, provenance identity flag, original sample timestamp, 15-second freshness and investigation deadline immediately before admission. Only completed, validated evidence reaches `apply_observation()`. FAILED and UNAVAILABLE observations do not call Bayes or alter beliefs.

Normalization errors, mismatched identity, malformed results, impossible evidence rejected by the Bayesian engine and late results are recorded as unsuccessful attempts, retaining available raw provenance. A bad completed record cannot become posterior evidence just because a collector labeled it completed.

### Duplicate and evidence-group handling

- Each diagnostic ID is consumed at most once per investigation, including failed attempts; there are no automatic retries.
- Admitted measurements are keyed by source, canonical target and original sample timestamp. The fingerprint deliberately excludes test ID and outcome, preventing relabeling the same sample as independent evidence.
- An admitted catalogue evidence_group blocks any remaining test in that group. Missing groups are ineligible. Failed observations do not consume a group because they provide no evidence.
- Selected results rejected as duplicates still consume the selected attempt and its estimate; rejected initial duplicates do not create new work or another charge.

These checks are conservative: separate groups do not prove conditional independence. The engine still assumes observations are conditionally independent given the initiating hypothesis. Runtime state and connectivity can share causes, timing and environment; the current synthetic tables do not model all residual dependence. Group exclusion prevents obvious aliases, not arbitrary statistical correlation. Multi-fault causality, dependency learning and calibrated stopping remain future research work.

### Initial evidence contract

`initial_evidence` accepts existing NormalizedObservation records for the two supported diagnostics, with original manager raw evidence under `metadata['execution_result']`. The supplied targets and worker endpoint/helper configuration must still be explicit. The adapter revalidates those raw records at current time, and supplied observation identity/outcome/timestamp must agree with the revalidated record.

Accepted unique completed initial records consume one attempt and the existing test's estimated energy/latency, and suppress fresh execution of that same test. This conservative convention includes already-acquired diagnostic evidence in the investigation's accounting. Stale, malformed, tampered, unsupported, duplicate or over-budget initial records are rejected without a Bayesian update or an extra charge; a stale record does not prevent a later fresh selected diagnostic.

This milestone does not automatically collect Netdata or add passive CPU evidence to the live model. Phase 1's CPU path remains available unchanged. General initial telemetry beyond the two supported raw diagnostic contracts requires an explicit future target/provenance integration, not a guessed mapping.

## Stopping and budgets

The live path calls the unchanged `stopping_decision()` with current beliefs, eligible remaining tests, attempted count, successful admitted count, estimated energy and StoppingConfig. Existing confidence, unknown abstention, attempt limit, no-tests, energy affordability and minimum-information-gain behavior is retained, including confidence precedence after a final useful attempt.

Additional live constraints are execution deadlines and evidence/capability eligibility. The overall deadline starts when the live investigation function begins, after catalogue loading. A test is not selected unless its configured per-action deadline fits the remaining time. Dispatch receives at most the remaining investigation time. Runtime verification's SDK request timeout is temporarily bounded and restored; the existing worker helper receives the absolute deadline, preserving its cleanup reserve. Results arriving too late are not admitted.

As in earlier phases, Docker request timeouts and cleanup are bounded best-effort external operations, not a hard real-time guarantee against a permanently unresponsive daemon. No unbounded retries or new helper-management controller were added. Existing cleanup-error metadata remains visible.

The two distinct cost/time concepts are exposed honestly:

- Predicted energy and latency from the catalogue drive planning/accounting.
- Actual action/elapsed durations and deadlines govern live execution.

Measured energy remains None. Neither the .55 J total estimate nor the policy score is a measured efficiency result.

## CLI and chronological trace

```bash
.venv/bin/python scripts/demo_live_investigation.py \
  --redis-id FULL_REDIS_ID \
  --worker-id FULL_WORKER_ID \
  --redis-host redis --redis-port 6379 \
  --helper-image sha256:FULL_CONTROLLED_HELPER_IMAGE_ID \
  --policy ratio --max-tests 2 --max-energy 1 --deadline 30
```

`--redis-name` and `--worker-name` are exact-name alternatives. Other controls include `--connection-timeout`, `--probe-deadline`, `--runtime-deadline`, `--confidence` and the existing four policy names. For weighted selection the CLI uses .02 bits/J and .01 bits/s; fixed order is Redis runtime then worker connectivity.

Output JSON includes the model ID/version/status, existing InvestigationResult, full chronological trace, admitted and rejected evidence, and measured elapsed time. Events show:

1. Initial probabilities and explicit research/conditional-independence assumptions.
2. Initial evidence admission/rejection.
3. Eligible/affordable tests, excluded reasons and remaining budgets.
4. Selected test, real planner IG/score, provisional J/s and resolved target.
5. Actual action phase/status and verification accounting.
6. Normalized Observation, provenance and admission reason.
7. Posterior, attempted/completed IDs and unattempted candidates.
8. Terminal decision, explanation and final beliefs.

Subsequent eligibility events distinguish usable candidates from merely unattempted IDs. ABSTAIN is a valid lifecycle completion, so the CLI exits 0 for a returned result, including budget/capability abstention; provider failures remain explicit in the trace. Configuration/runtime exceptions preventing a lifecycle result exit 1. Exit 0 alone is not evidence that a real diagnostic succeeded; inspect the observations and admitted evidence.

The CLI never stops, restarts, removes or modifies application containers. Only the existing TCP helper lifecycle creates/removes its own disposable helper. No remediation is offered or executed.

## Tests and verification

The 37 new tests cover real-policy dispatch, correct targets, pure eligibility checks, both selected verification paths without repeat execution, timeout restoration, missing configuration, provider failure, attempt/energy/deadline limits, known/unknown stopping, insufficient information, initial evidence accounting, tampering/staleness, duplicates, evidence groups, normalized posteriors and deterministic replay preservation.

An initial new unknown-leader test incorrectly expected evidence collection with a probability-one prior. The existing minimum-success/zero-information rule correctly abstained before any attempt. The new test was corrected to start with a nondegenerate prior and reach confident unknown after valid evidence. No mathematical or stopping implementation was changed. An in-flight combined run using the previous test version reported 1 failed and 328 passed; the corrected test independently passed before the final full rerun.

Final test command:

```bash
.venv/bin/python -m pytest -q \
  tests/test_engine.py tests/test_validation.py \
  tests/test_catalogue_loader.py tests/test_normalizer.py \
  tests/test_real_observation_integration.py \
  tests/test_collector_manager.py tests/test_diagnostic_adapter.py \
  tests/test_worker_redis_connectivity.py tests/test_live_investigation.py
```

**Final result: 329 passed in 51.54s — all 292 existing tests plus 37 new tests.** Catalogue validation also passed: 15 hypotheses, 20 tests, 19 relationship groups and 300 complete synthetic distributions. Explicit paths avoid importing root-level collector smoke scripts.

## Actual autonomous Docker investigations

Approved live verification created an isolated internal Docker network, one disposable Redis server with persistence disabled and one lab worker network anchor. It reused the cached controlled helper image. No ports were published and no existing application containers were modified. The fixture removed its worker, Redis and network afterward; each TCP action removed its own helper.

The CLI autonomously selected both operations, without pre-executing candidates:

| Scenario | First observation | Second observation | Final decision | Estimated total | Actual elapsed |
|---|---|---|---|---|---|
| Redis port 6379 reachable | running | connected | ABSTAIN: maximum test count reached | .55 J, .37 s | 5.404 s |
| Same Redis host, closed port 6380 | running | refused | ABSTAIN: maximum test count reached | .55 J, .37 s | 5.407 s |

Both posteriors summed to 1.0. The large difference between predicted .37 seconds and actual approximately 5.4 seconds reinforces that these costs are illustrative, not calibrated measurements.

Example reachable trace values:

```text
Initial P(redis_process_stopped): 0.099009901
Selected redis_runtime_state:
  IG 0.3180403900 bits, ratio score 3.1804039003 bits/estimated J
  Observation: running
  Updated P(redis_process_stopped): 0.0066085118
Selected worker_redis_connectivity:
  IG 0.5626057181 bits, ratio score 1.2502349291 bits/estimated J
  Observation: connected
  Updated P(redis_process_stopped): 0.0004936010
Stop: ABSTAIN, maximum test count reached
```

For the closed-port run, the final Redis-process-stopped posterior was 0.0481159928 after the refused observation. These are synthetic-model updates from real observations, not demonstrated root-cause accuracy. In both runs exactly two selected logical diagnostic actions supplied exactly two admitted observations; verification was not followed by duplicate execution. TCP results reported helper_removed=True.

Live sample timestamps:

```text
Reachable runtime: 2026-11-14 22:44:30.598560+00:00
Reachable TCP:     2026-11-14 22:44:35.662654+00:00
Closed-port runtime: 2026-11-14 22:44:37.625024+00:00
Closed-port TCP:     2026-11-14 22:44:42.672229+00:00
```

Full temporary execution records: `/tmp/infrasage-phase2b2-live.json`. These are actual Docker executions, not outputs from mocked tests. The worker was a controlled lab network anchor, not a claimed deployed Voting App workload. No incident detector, production accuracy benchmark or measured energy benefit is established by this demonstration.

## Remaining work

- Calibrate likelihoods, priors, stopping thresholds and expected incremental costs against labeled experiments.
- Add explicit incident context and broader initial telemetry contracts before consuming CPU/alert evidence automatically.
- Model correlation beyond conservative evidence-group exclusion and preserve evidence identity across persisted investigations.
- Improve capability discovery, durable history and cancellation as separately scoped work.
- Connect more diagnostics only after their bindings, outcomes and costs are verified.

The two-operation live loop is complete without changing the mathematical engine, selecting unsupported diagnostics or adding a competing investigation controller.
