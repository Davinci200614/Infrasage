# InfraSage — Collector Branch Integration Audit

Audit date: 2026-11-14. Source inspection was read-only; this document was subsequently saved at the user's request. No application changes, branch switches, merges, commits or dependency installations were performed.

## Contents

1. [Executive summary](#1-executive-summary)
2. [Branches, commits and changed files](#2-branches-commits-and-changed-files)
3. [Implemented components and execution contracts](#3-implemented-components-and-execution-contracts)
4. [Phase 2A coverage matrix](#4-phase-2a-coverage-matrix)
5. [Conflicts and compatibility with Phase 1](#5-conflicts-and-compatibility-with-phase-1)
6. [What should be reused](#6-what-should-be-reused)
7. [What should be adapted](#7-what-should-be-adapted)
8. [What remains missing](#8-what-remains-missing)
9. [Recommended integration order](#9-recommended-integration-order)
10. [Verification and required tests](#10-verification-and-required-tests)

## 1. Executive summary

**The collector branch implements a useful collection/execution foundation, but Phase 2A is only partially complete. Reuse it with targeted adaptations; preserve Phase 1 and the mathematical engine.**

The branch contains:

- A concrete `CollectorManager` implementation.
- A `DiagnosticAdapter` that constructs the existing `Observation` model.
- Three Docker operation routes: runtime state, restart history and worker logs.
- Expanded Docker metadata, event analysis, log signatures and Netdata disk collection.

The main blockers are:

1. No worker-to-Redis connectivity operation.
2. The adapter does not validate execution-result test or target identity.
3. No configured outcome mappings or compatible catalogue bindings.
4. No automated tests for the new manager or adapter.
5. No connection to the planner/investigation lifecycle.

The audit did not modify the working tree. In-memory contract probes used fake provider data, not live Docker operations.

## 2. Branches, commits and changed files

| Item | Verified value |
|---|---|
| Current branch | `feature/catalogue-observation-integration` |
| Current HEAD | `8c597ae634edef3c5f68b6df9eb7b3038147967d` |
| Collector branch | `origin/feature/docker-collector` |
| Collector tip | `838ab100a64d3e173844739e21fffe771d9a03f5` |
| Common ancestor | `82fa0ecde7735c29866629611fccfc8fa4989a45` |

A read-only `git ls-remote --heads origin feature/docker-collector` confirmed that GitHub's collector branch also pointed to `838ab10` at audit time. No fetch was performed.

There is one collector-side commit since the common ancestor:

```text
838ab10 Complete infrastructure collectors and diagnostic adapter
```

Its changes, measured from the common ancestor, are:

| File | Changes |
|---|---:|
| `backend/collectors/docker.py` | +49 / −11 |
| `backend/collectors/docker_events.py` | +99 / −17 |
| `backend/collectors/docker_logs.py` | +107 / −31 |
| `backend/collectors/netdata.py` | +194 / −21 |
| `backend/core/collector_manager.py` | New, 440 lines |
| `backend/core/diagnostic_adapter.py` | New, 252 lines |

Total: six files, 1,141 insertions and 80 deletions. No tests, catalogues, dependencies or mathematical modules were changed by this commit.

The current branch has mathematical/catalogue/Phase 1 commits absent from the collector branch. Its existing uncommitted edit to `backend/collectors/netdata.py` was preserved. This working-tree edit overlaps a file changed by the teammate and must be accounted for during any later authorized integration.

References below to manager/adapter code refer to the collector-branch snapshot, where those files exist, rather than files already integrated into the current branch. Inspect them without switching branches:

```bash
git show origin/feature/docker-collector:backend/core/collector_manager.py
git show origin/feature/docker-collector:backend/core/diagnostic_adapter.py
git diff HEAD...origin/feature/docker-collector
```

## 3. Implemented components and execution contracts

### CollectorManager

Module: `backend/core/collector_manager.py`, class at line 5.

```python
CollectorManager(catalogue, collection_orchestrator=None)
collect_snapshot(self, target=None)
execute_test(self, test, target)
```

Required inputs:

- `catalogue` must be a dictionary shaped as `{"tests": {test_id: definition}}` (`_get_test_definition`, lines 76–86).
- `test` may be an identifier string, an object with `.identifier`, or a dictionary containing `identifier`/`id` (`_get_test_id`, lines 59–70). A dictionary containing only `test_id` is not accepted.
- `target` must be a dictionary. Docker operations require `container_id` or `container_name`.

Return structure (`_result`, lines 429–440):

```python
{
    'test_id': '...',
    'status': 'completed' | 'failed' | 'unavailable',
    'data': ...,
    # optional:
    'error': ...,
}
```

It correctly keeps execution separate from Bayesian reasoning and planning. `execute_test()` checks `availability.live is True`, `execution.binding_status == 'verified'`, and a truthy `execution.live_binding` (lines 152–172).

**The binding contents are never used or validated.** Dispatch uses the logical operation and caller-supplied target. Only `provider == 'docker'` is supported (lines 184–196); Netdata and precision providers return UNAVAILABLE.

| Operation | Provider call | Result and limitations |
|---|---|---|
| `_execute_runtime_state`, line 258 | `collect_snapshot()` → `CollectionOrchestrator.collect_initial()` | Returns matching Docker container dictionary. Performs a complete Netdata/Docker collection rather than targeted inspection |
| `_execute_restart_history`, line 323 | `collect_on_demand('events', ..., event_actions=['die', 'start'])` | Returns event collector output through `_convert_collection_result()` |
| `_execute_worker_logs`, line 353 | `collect_on_demand('logs', ..., tail=100, since=None, until=None)` | Returns log collector output through `_convert_collection_result()` |

The corresponding logical operations are `container_process_state`, `container_restart_history`, and `read_bounded_worker_logs` (`_execute_docker_test`, lines 210–234).

Error behavior:

- Invalid test IDs, unknown tests, invalid target shape, disabled/unverified bindings and unsupported operations/providers return UNAVAILABLE.
- Dispatch exceptions and unsuccessful on-demand provider results return FAILED.
- Missing runtime containers return UNAVAILABLE.
- `_convert_collection_result()` propagates collector errors and rejects missing data or `success=False` (lines 381–423).
- Malformed catalogue definitions can raise before the dispatch exception handler; this is not fully defensive schema handling.

Additional limitations:

- `collect_snapshot(target)` ignores `target` (lines 36–52).
- Runtime matching accepts ID prefixes and returns the first match without rejecting ambiguity (lines 290–316).
- Default construction instantiates `CollectionOrchestrator`, which immediately constructs DockerCollector. Docker connection failure can occur before execution-policy checks.
- No manager/adapter execution tests or callers were found in the branch.

### DiagnosticAdapter

Module: `backend/core/diagnostic_adapter.py`, class at line 5.

```python
DiagnosticAdapter(outcome_mappings=None)
adapt(self, test, execution_result, target)
```

It requires the existing `DiagnosticTest`, a result dictionary and a target dictionary, and returns the existing `Observation`. Its lowercase status comparisons are compatible with the existing `ExecutionStatus` StrEnum.

| Mapping type | Required configuration/data | Behavior |
|---|---|---|
| `field` | Mapping `field`, `values`; corresponding raw field | Maps a raw value to a declared outcome (`_resolve_field`, line 98) |
| `restart_activity` | `restart_count`, optional field/threshold configuration | Maps counts to `none`, `recent`, `repeated` (`_resolve_restart_activity`, line 117) |
| `worker_error_signature` | `error_signatures` counters | Returns one category; multiple detected categories become UNAVAILABLE (`_resolve_worker_errors`, line 150) |

FAILED and UNAVAILABLE statuses remain outcome-free. Unknown execution status, absent/malformed raw data, missing mappings and unsupported outcomes become UNAVAILABLE. Wrong top-level argument types raise TypeError. Observation construction can reject invalid duration/energy.

No mappings are supplied elsewhere in the branch. `DiagnosticAdapter()` therefore cannot produce completed diagnostic observations by default.

`_get_target_identifier()` chooses container name, container ID, service, host or ID, falling back to the generic string `infrastructure` (lines 217–231). `_build_observation()` defaults duration to zero and energy to None, without passing a provider measurement timestamp (lines 233–252).

### Collector extensions

| Component | Implemented additions | Verification status |
|---|---|---|
| `DockerCollector.collect()` | Runtime flags, exit/error details, healthcheck configuration and network names; existing fields retained | Source inspected; new fields not covered by new automated tests |
| `DockerEventsCollector.collect()` | Action counts and inferred `die → start` restart counts, timestamps and query parameters | Source inspected; no new assertion-based coverage |
| `DockerLogsCollector.collect()` | Redis/PostgreSQL/general error-signature counters | Source inspected; mappings/signatures not empirically validated |
| `NetdataCollector.collect()` | Disk discovery, disk samples and derived metrics; existing CPU raw shape retained | Source inspected; Phase 1 compatibility not rerun against this version |

These are implemented code paths, not demonstrated new diagnostic integrations. No new diagnostic capability registry, catalogue bindings or live-verification artifact accompanies them.

## 4. Phase 2A coverage matrix

| Requirement | Classification | Finding |
|---|---|---|
| Lightweight execution manager | Implemented but needs modification | Useful manager; catalogue/binding/target contracts need tightening |
| Separation from Bayesian reasoning | Already implemented and compatible | Manager/adapter do not update beliefs or select tests |
| Initial collection reuse | Already implemented and compatible | Delegates to existing CollectionOrchestrator |
| Test-ID-to-operation routing | Partially implemented | Three Docker operations only |
| Netdata snapshot collection | Implemented but needs regression verification | Expanded collector; no manager-side Netdata execution route |
| `redis_runtime_state` | Partially implemented | Runtime operation exists; provider and mapping configuration do not match current catalogue |
| `worker_redis_connectivity` | Missing | No worker-context TCP probe or route |
| Existing Observation construction | Implemented but needs modification | Compatible constructor; identity/freshness/provenance checks missing |
| Execution failure separation | Partially implemented | Core status handling correct; some errors escape or lose context |
| Verified-live availability gate | Partially implemented | Flags checked; binding semantics/capability verification absent |
| Capability discovery | Partially implemented | Network/disk chart discovery, not diagnostic capability registration |
| Provenance and freshness | Partially implemented | Some provider metadata exists; adapter discards it and performs no freshness checks |
| Duplicate/correlated evidence control | Missing | No consumed-evidence or evidence-group handling |
| Catalogue-loader integration | Missing | Manager cannot consume LoadedCatalogues directly |
| Planner-selected execution | Missing | No caller connects planner selections to manager |
| Automated diagnostic execution tests | Missing | No manager/adapter tests or callers found |

## 5. Conflicts and compatibility with Phase 1

### Catalogue input mismatch

The manager expects `{'tests': {test_id: raw_definition}}`; Phase 1 returns `LoadedCatalogues` from `backend/diagnosis/catalogue_loader.py::load_catalogues`.

Passing LoadedCatalogues directly produces “Test not found in catalogue.” A compatible existing representation is:

```python
loaded.metadata['tests']
```

`loaded.test_metadata` alone lacks the expected outer `tests` key. Use a small explicit boundary rather than replacing the loader. Preserve the distinction between numerical DiagnosticTest objects and executable companion definitions.

### Redis catalogue mismatch

The current `backend/diagnosis/catalogues/tests.yaml` declares:

| Test | Provider | Logical operation | Current live status |
|---|---|---|---|
| `redis_runtime_state` | `netdata` | `container_process_state` | Disabled; binding null and unverified |
| `worker_redis_connectivity` | `precision` | `worker_to_redis_tcp` | Disabled; binding null and unverified |

The manager supports only Docker. Consequently both tests remain unavailable under current restrictions. Enabling flags alone would still not make those providers supported. Redis runtime state needs an explicitly verified Docker binding/fallback and outcome mapping. Connectivity needs an actual implementation. Docker state `exited` must map explicitly to catalogue outcome `stopped`; that mapping is absent.

### Adapter identity defect — highest priority

`DiagnosticAdapter.adapt()` uses `test.identifier` without checking `execution_result['test_id']` (lines 26–29). It also derives the observation target from the supplied target dictionary without validating the returned container identity.

An in-memory probe confirmed that a result labeled for another test and another container becomes a COMPLETED Redis observation after supplying a field mapping. The downstream Bayesian engine cannot catch this mismatch because the adapter has already relabeled the evidence.

Fix identity validation before admitting adapter output into inference.

### Freshness and provenance mismatch

`DiagnosticAdapter._build_observation()` does not pass a provider measurement timestamp, uses Observation's default current timestamp, drops error/provenance metadata from its return value, and defaults duration to `0.0`; the manager does not measure duration.

A probe confirmed that stale input evidence can become a newly timestamped completed observation. Phase 1's `backend/processing/normalizer.py::normalize_cpu_observation` already preserves timing distinctions and rejects stale CPU evidence. Keep it intact and use a compatible companion provenance envelope for Docker diagnostics.

### Mathematical files on the collector branch

The collector branch contains empty `backend/core/models.py`, `backend/diagnosis/bayesian.py`, and `backend/optimization/planner.py`, plus an old scaffold `Orchestrator` in `backend/core/orchestrator.py`.

These are inherited branch history, not changes introduced by `838ab10`. Its adapter cannot import successfully on that branch alone because the required mathematical classes are absent. This does not imply that a correctly reviewed integration of the six-file delta must remove the current engine.

Preserve current mathematical files. Do not replace them with collector-branch snapshots.

### Dependencies and overlapping files

The new commit changes no dependencies. The collector branch's inherited requirements are a broad pinned environment export including FastAPI and related packages, while omitting the current pytest/PyYAML declarations. Keep the current focused requirements; these six modules do not justify replacing them.

The teammate does not edit the Phase 1 loader, normalizer, catalogues or protected engine in its new commit. The direct overlapping working-tree file is `collectors/netdata.py`, whose pre-existing local edit must be preserved. Runtime/schema compatibility still requires testing even where Git shows no textual conflict.

## 6. What should be reused

Preserve unchanged:

- Current mathematical engine, planner and simulation/replay lifecycle.
- Phase 1 catalogue loader, CPU normalizer and live demonstration.
- Existing CollectionOrchestrator collection coordination.
- The manager's separation of execution from inference.
- Its default-deny execution policy.
- Additive Docker metadata/provider result structures, subject to regression tests.

Keeping CollectionOrchestrator beneath CollectorManager does not create a second investigation controller: it currently coordinates collection only. Avoid replacing functioning provider code or building a parallel collection system.

## 7. What should be adapted

Before integration:

1. Validate result test identity and canonical target identity.
2. Preserve source, sampling time, retrieval time, execution errors and duration in a companion envelope.
3. Connect manager input to the existing loader's execution metadata.
4. Define explicit outcome mappings without duplicating the Phase 1 CPU normalizer.
5. Validate binding contents and applicability rather than checking truthiness.
6. Reject ambiguous container matches and generic target fallbacks for diagnostic evidence.
7. Prefer targeted runtime inspection over recollecting all infrastructure for each state test.

Additional concerns requiring focused tests:

- `_resolve_worker_errors()` treats missing counters as zero: an empty signature dictionary becomes `none`.
- Restart detection counts `die → start` sequences, which can also represent manual lifecycle operations; time-window boundaries can omit part of a sequence.
- The event manager filters only die/start, so direct restart events are not consumed by that operation.
- Log regexes are provisional. One line matching both Redis and PostgreSQL is assigned Redis priority by the collector, even though the adapter rejects multiple positive categories across counters.
- DockerCollector skips per-container API errors; manager runtime lookup may report a skipped container as unavailable rather than retain the original error.

## 8. What remains missing

The minimal Redis Phase 2A path still needs worker-context Redis connectivity, verified bindings, configured normalization mappings, diagnostic capability/eligibility checks, evidence identity/freshness/deduplication, planner-to-manager dispatch, and integration with the single investigation lifecycle.

Netdata's expanded full collection creates a Phase 1 regression risk: additional sequential disk requests may delay CPU consumption beyond the current 15-second freshness limit. Its derived CPU aggregate still sums whichever dimensions are present; keep Phase 1's strict raw-dimension validation rather than trusting that aggregate.

No automated manager/adapter tests, new selected-test execution demonstration, or live verification evidence accompanies the new commit. These absences prevent classifying it as completed Phase 2A.

## 9. Recommended integration order

1. Retain the current branch as the mathematical/Phase 1 foundation. Review only the six-file collector delta and preserve the uncommitted Netdata edit.
2. Integrate manager/adapter contracts first, fixing identity validation, provenance, binding handling and mappings with fake providers.
3. Verify Redis runtime state through a narrow Docker operation and one explicit catalogue binding. Keep all other unverified operations disabled.
4. Implement worker-to-Redis connectivity, preserving connected/refused/timeout outcomes separately from tool, permission and transport execution failures.
5. Connect planner selection to execution and normalization, retaining lifecycle ownership in the existing investigation layer and preserving simulation/replay behavior.
6. Review optional disk/log/event expansion separately so it does not block or regress the minimal Redis path.

No architectural rewrite is necessary. This order is a recommendation for a later authorized implementation, not work performed by this audit.

## 10. Verification and required tests

### Checks performed during the audit

- Read-only Git status, refs, merge-base, logs, tree inspection and branch diffs.
- Read-only remote-tip verification using `git ls-remote`; the initial sandbox DNS failure was retried with approved external network access.
- Inspected all six changed modules, existing collection coordination, relevant catalogues, Phase 1 loader/normalizer, dependencies and root smoke scripts.
- Parsed all six changed Python modules successfully.
- Loaded selected branch class definitions in memory with current mathematical classes and injected fake collection results; no branch checkout or file extraction was performed.

The in-memory probes demonstrated:

| Probe | Actual result |
|---|---|
| Pass LoadedCatalogues directly to manager | UNAVAILABLE: test not found |
| Pass compatible metadata with current live restrictions | UNAVAILABLE: live execution disabled |
| Supply hypothetical verified Docker binding in memory | Runtime route returns completed raw container data |
| Use adapter without mappings | UNAVAILABLE |
| Supply mismatched test ID, different container and stale evidence with a field mapping | Adapter returns a COMPLETED Redis observation with a new timestamp |
| Resolve empty log signature counters | Returns `none` |

These probes are audit evidence, not a committed automated test suite or real-operation verification. No live Docker operations or pytest run occurred during this audit. Existing root smoke scripts instantiate collectors during import and must not be included in unrestricted discovery. The previously passing 150 Phase 1 tests do not validate these branch changes.

### Tests required after integration

- All 150 original Phase 1 tests using explicit paths.
- Manager live/binding/provider gates with zero provider calls when denied.
- Exact test/target identity and ambiguous-container-ID rejection.
- Runtime state vocabulary mapping, including exited→stopped and restarting.
- Missing/failed/unavailable execution preserving beliefs.
- Sampling-time freshness, provenance and duration preservation.
- Missing log counters, mixed signatures and restart-window edge cases.
- Worker-context Redis connectivity outcomes versus probe execution failures.
- Planner-selected ID dispatch and duplicate/correlated evidence prevention.
- Opt-in actual Redis diagnostics and a fresh Phase 1 Netdata CLI run.

**Recommendation:** reuse the teammate's manager, adapter and collector work as an untested execution foundation. Fix the adapter identity boundary first, then establish verified bindings and the missing connectivity operation before connecting the investigation loop.
