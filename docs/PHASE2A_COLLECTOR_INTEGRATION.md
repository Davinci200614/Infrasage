# Phase 2A — Hardened CollectorManager and real Docker runtime observation

Implemented 2026-11-14 on `feature/catalogue-observation-integration`, starting from HEAD `8c597ae`. Teammate source: `origin/feature/docker-collector`, commit `838ab100a64d3e173844739e21fffe771d9a03f5`.

## Outcome

The following path now works with real Docker evidence:

```text
redis_runtime_state + explicit Docker target
  → CollectorManager.verify_runtime_binding()  [read-only, targeted inspection]
  → process-local research binding
  → CollectorManager.execute_test()            [new, targeted inspection]
  → DiagnosticAdapter.adapt_with_metadata()
  → existing Observation + existing NormalizedObservation provenance envelope
```

Both running and stopped Redis states were exercised on a temporary, isolated Redis container. All **231 tests passed**, including all 150 existing tests. The Phase 1 live Netdata-to-Bayesian demonstration still succeeds. No planner dispatch or live investigation lifecycle was added.

## Preservation and source reuse

The two teammate modules were copied directly from `838ab10` before applying targeted hardening. CollectorManager retains its test-ID resolution, operation dispatcher, supplementary log/event helpers, collection-result conversion and raw result builder. DiagnosticAdapter retains its field/nested-field mapping, outcome resolver and restart mapping. Its Observation builder is reused with an explicit timestamp requirement. Missing log counters no longer default to zero.

SHA256 checks confirmed these files are byte-for-byte unchanged from task start:

- `backend/core/models.py`
- `backend/diagnosis/bayesian.py`
- `backend/optimization/planner.py`
- `backend/core/orchestrator.py`
- `backend/diagnosis/catalogue_loader.py`
- `backend/processing/normalizer.py`
- `backend/collectors/netdata.py`, including its pre-existing local whitespace edit
- `docs/COLLECTOR_BRANCH_INTEGRATION_AUDIT.md`, the pre-existing untracked audit

The original Docker `collect()` and CPU calculation APIs remain unchanged. No catalogue, requirements, frontend, API, twin or energy code was modified. No branches were switched or merged, and no commit or push was performed.

## Updated files

| File | Change |
|---|---|
| `backend/core/collector_manager.py` | Teammate manager integrated; loader compatibility, lazy provider creation, one scoped verified runtime binding, strict routing/identity checks and targeted execution |
| `backend/core/diagnostic_adapter.py` | Teammate adapter integrated; result test/target identity, provider/binding/timestamp validation, explicit runtime mappings, preserved provenance and error handling |
| `backend/collectors/docker.py` | Added exact identity validators and targeted `inspect_runtime()`; existing methods preserved |
| `scripts/demo_live_diagnostic.py` | Explicit-target, read-only verification/execution/normalization CLI |
| `tests/test_collector_manager.py` | Routing, policy/binding gates, identity, provider errors, target replacement, stale verification and mocked CLI checks |
| `tests/test_diagnostic_adapter.py` | Mapping, test/target identity, timestamps, errors, measurements, provenance and existing Bayesian-consumption checks |
| `docs/PHASE2A_COLLECTOR_INTEGRATION.md` | This execution contract and verification report |

The teammate's broader Netdata disk collection and Docker log/event extensions were not imported. Their unrelated cost and regression risks are unnecessary for this milestone. Supplementary manager helper methods remain present from the teammate implementation, but public live dispatch rejects their operations in Phase 2A.

## Exact interfaces

```python
# Existing class, with additive targeted inspection:
DockerCollector.inspect_runtime(
    self, *, container_id=None, container_name=None,
) -> dict

CollectorManager(
    catalogue, collection_orchestrator=None, *, docker_collector=None,
)
CollectorManager.collect_snapshot(self, target=None) -> dict
CollectorManager.verify_runtime_binding(self, test, target) -> dict
CollectorManager.execute_test(self, test, target) -> dict
CollectorManager.close(self)

DiagnosticAdapter(outcome_mappings=None, *, freshness_max_seconds=15)
DiagnosticAdapter.adapt(self, test, execution_result, target) -> Observation
DiagnosticAdapter.adapt_with_metadata(
    self, test, execution_result, target, *, retrieved_at=None,
) -> NormalizedObservation
```

`test` uses the teammate's accepted identifier string, DiagnosticTest object with `.identifier`, or dictionary with `identifier`/`id`. `target` must contain an exact `container_name` or a full 64-character lowercase hexadecimal `container_id`. If both are provided they must identify the same container. ID prefixes, wildcard names, whitespace names, generic targets and absent identities are rejected.

### Loader compatibility

Pass `load_catalogues(research_mode=True)` directly to CollectorManager. It reads execution metadata from LoadedCatalogues.test_metadata while keeping mathematical DiagnosticTest objects separate. The teammate's `{'tests': {test_id: definition}}` dictionary input also remains supported for execution-policy evaluation. Automatic binding verification requires explicit LoadedCatalogues research mode.

No loader public API or catalogue documents were changed. Passing a numerical model to the manager does not implicitly permit execution.

### Verification and availability

Before verification, the Redis test is unavailable. `verify_runtime_binding()` only permits `redis_runtime_state` with catalogue logical operation `container_process_state` and research/simulation availability. It performs an actual targeted inspection, validates the returned identity, source, supported state and timestamp, then creates a deep-copied **manager-local** definition with:

```python
{
    'provider': 'docker',
    'operation': 'container_process_state',
    'container_id': '<full actual ID>',
    'container_name': '<actual exact name>',
    'docker_endpoint': '<SDK endpoint>',
    'verified_at': '<actual UTC inspection observation time>',
    'freshness_max_seconds': 15,
}
```

Only that local definition becomes executable. The original catalogue still has all 20 live flags false and all live bindings null. Nothing is written back to YAML. Model calibration and production-inference restrictions remain unchanged.

Execution requires a verified, structurally valid binding, matching provider/operation/test, matching explicit target and unexpired verification. It performs a second inspection and checks that container ID/name and Docker endpoint still match the binding. Replacement of a container under the same name is rejected. The 15-second binding lifetime is intentionally conservative; verify again before later execution. A manager holds one current Redis binding, not a persistent capability registry or concurrent multi-target service.

Provider creation is lazy. A disabled-test request does not instantiate Docker or contact Netdata. Existing CollectionOrchestrator remains available for explicit batch collection; runtime inspection does not call it. `close()` closes only a Docker client created by the manager, leaving injected clients under caller ownership.

## Raw execution contract

Successful targeted inspection returns:

```python
{
    'test_id': 'redis_runtime_state',  # attached by CollectorManager
    'source': 'docker',
    'status': 'completed',
    'target': {'container_id': '<full ID>', 'container_name': '<exact name>'},
    'target_identifier': 'docker:<full ID>',
    'data': {
        'id': '<full ID>', 'name': '<exact name>', 'status': 'running',
        # Runtime flags, exit/error details, start/finish times and image metadata.
    },
    'observed_at': '<UTC time when this inspect returned>',
    'retrieved_at': '<UTC collection completion time>',
    'duration_seconds': '<measured targeted inspection duration>',
    'docker_endpoint': '<SDK endpoint>',
    'binding': {...},
}
```

`containers.get()` performs Docker inspection; no container stats, whole-container listing, network metrics or Netdata requests are needed. The receipt time is the sampling time of this current inspect operation, not the container's StartedAt timestamp. StartedAt/FinishedAt remain historical runtime metadata and are not misrepresented as current evidence time.

Missing containers and invalid/ambiguous targets return UNAVAILABLE. Docker API/connection failures and malformed provider responses return FAILED. No failure generates a stopped outcome. Identity changes retain the returned provider evidence and binding in the failed result for inspection.

## Normalization and provenance

Default explicit Docker mappings are:

| Docker Status | Catalogue outcome |
|---|---|
| `running` | `running` |
| `exited` | `stopped` |
| `restarting` | `restarting` |

Paused, dead, created, removing and unknown states are not silently mapped to stopped; they are unavailable for this outcome protocol. The test vocabulary is checked before completion.

The adapter validates the result test ID, source, requested versus canonical container identity, raw data identity, canonical target string, binding identity and Docker endpoint. A mismatched result cannot become completed evidence. Completed results containing provider errors are rejected.

Both timestamps and the binding verification time must be timezone-aware and consistently ordered:

```text
verified_at ≤ observed_at ≤ provider retrieved_at ≤ adapter processing time
```

Evidence and binding age must remain within their freshness limits; missing, stale, future, naive and inconsistent timestamps produce outcome-free unavailable observations. Completed observations retain observed_at as Observation.timestamp. Failed/unavailable observations use the real processing-attempt time, explicitly labeled in companion metadata; the original provider timestamps and error payload remain in `metadata['execution_result']`.

`adapt()` preserves the teammate's Observation return API. Prefer `adapt_with_metadata()` when retaining evidence: it uses Phase 1's existing NormalizedObservation envelope, including the original execution result, source, measurement timestamp, retrieval time, identity-verification flag and reason. No new mathematical dataclass or alternate normalizer replaces Phase 1.

Duration is measured around targeted collection. Energy remains None because no meter exists. Invalid duration/energy cannot produce completed evidence; original invalid fields remain in provenance. Existing `apply_observation()` accepts these Observations unchanged; deterministic tests confirm successful evidence updates beliefs and execution failures do not.

## CLI usage

From the repository root:

```bash
.venv/bin/python scripts/demo_live_diagnostic.py --container-name my-redis
.venv/bin/python scripts/demo_live_diagnostic.py --container-id FULL_64_CHARACTER_ID
.venv/bin/python scripts/demo_live_diagnostic.py --container-name my-redis --expected-outcome running
```

The target is required. The CLI does not create, start, stop or remove the supplied container. It verifies the operation, executes a new inspection, normalizes the result and prints JSON with verification, observation, provenance, model status and success/failure. Exit 0 requires a completed observation and, when supplied, the expected outcome. It does not run Bayes, the planner or investigation orchestration.

The operator assigns the explicit container to the Redis component; the CLI reports image metadata but does not infer application topology or prove that an arbitrary named container runs Redis. The live verification below used an actual Redis image.

## Verification results

| Check | Actual result |
|---|---|
| Initial focused manager/adapter suite | 72 passed in 4.94s |
| All 150 existing tests, explicit paths | 150 passed in 32.80s |
| Final combined suite after additional hardening/CLI cases | **231 passed in 35.49s**: 150 existing plus 81 new |
| Catalogue validation | PASS: 15 hypotheses, 20 tests, 19 relationship groups, 300 synthetic distributions; live-disabled warning retained |
| Live Redis running runtime diagnostic | Exit 0; completed `running` observation |
| Live Redis stopped runtime diagnostic | Exit 0; completed `stopped` observation from Docker status `exited` |
| Existing Phase 1 live Netdata CLI | Exit 0; real CPU 13.4043886%, outcome normal, posterior sum 1.0 |
| Protected-file SHA256 checks | All protected mathematical and Phase 1 files unchanged |
| Docker diff whitespace check with original CRLF convention | `git -c core.whitespace=cr-at-eol diff --check -- backend/collectors/docker.py` passed |

Final explicit automated command:

```bash
.venv/bin/python -m pytest -q tests/test_engine.py tests/test_validation.py tests/test_catalogue_loader.py tests/test_normalizer.py tests/test_real_observation_integration.py tests/test_collector_manager.py tests/test_diagnostic_adapter.py
```

Root-level network/Docker smoke scripts were not collected. Automated tests use deterministic fake Docker responses and require no daemon. CLI tests exercise the actual manager/adapter with a mocked provider, independently of the live checks.

### Real Redis exercise

Docker was initially reachable only after approved access outside the socket sandbox. The environment initially contained only a stopped hello-world container and no Redis image. With approval, the verification pulled `redis:7-alpine` and created a uniquely named temporary Redis container with no network, no published ports, read-only root filesystem, no persistence and a 128 MiB memory limit.

The CLI was run twice with its full container ID: once while running and again after stopping this disposable container. Both runs returned completed observations. The temporary container was removed in a finally block; the original hello-world container was not modified. The downloaded image remains cached.

```text
Image ID: sha256:7f7d30b7fdd09467bac58198a1f166dd69789d348d4fe859cccb20e0cf287fbe
Container ID: bbeb4806f13fdecded5cec8c528ac72e762ac3f7438824c1c67ed3b0da01b917
Running observation: 2026-11-14 21:43:24.749895+00:00
Stopped observation: 2026-11-14 21:43:26.559391+00:00
```

Full temporary results: `/tmp/infrasage-phase2a-live.json`; Phase 1 rerun: `/tmp/infrasage-phase2a-netdata.json`. These are execution records, not committed datasets or calibrated probability evidence. No Python dependencies were installed.

## Remaining limitations and next work

- Only Redis runtime state is enabled through local verification; worker-to-Redis connectivity and all other selected-test operations remain disabled/unimplemented for this phase.
- No planner execution loop, orchestration change, persistence, deduplication service or capability-discovery registry was added.
- This is a Docker runtime observation, not Redis PING, network connectivity or application diagnosis.
- Canonical identity, freshness and provenance do not authenticate a hostile caller who fabricates in-process dictionaries. The boundary assumes trusted application code and provider access.
- Research probability parameters remain synthetic; runtime inspection success does not establish calibrated root-cause probabilities.
- Verification and execution each inspect once; state can legitimately change between them. Container identity is pinned, while the execution returns the newly observed runtime state.
- Teammate log/event helpers remain deferred and are not claimed as verified live diagnostics.

Next implement a bounded worker-context Redis connectivity operation with the same identity/error/provenance contract. Then, under separate authorization, connect existing planner selections to this manager while retaining single lifecycle ownership and evidence-group/deduplication checks.
