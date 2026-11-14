# Phase 1 — Catalogue loader and real Netdata observation integration

Implemented and verified on 2026-11-14 against the current working tree. This supersedes the earlier audit's placeholder description for the loader and normalizer. Collectors and CollectionOrchestrator have also been implemented by the team since that audit; this phase reuses them unchanged.

## Implemented scope

```text
Local Netdata Agent
  → existing NetdataCollector.collect()
  → normalize_cpu_observation()
  → existing Observation(COMPLETED, normal/high/critical)
  → existing apply_observation()
  → normalized posterior
```

This is one observation and one Bayesian update, with no planner dispatch or new investigation controller. All probability parameters and CPU thresholds remain explicitly provisional research assumptions. Real evidence does not make the resulting posterior a calibrated diagnosis.

## Files changed

| File | Change |
|---|---|
| `backend/diagnosis/catalogue_loader.py` | Strict three-file loader, duplicate-key detection, model conversion, retained metadata and explicit research gate |
| `backend/processing/normalizer.py` | Linux Netdata CPU dimension validation, catalogue thresholds, timestamp/freshness checks, existing Observation construction, companion provenance |
| `backend/diagnosis/catalogues/tests.yaml` | Added only the `host_cpu_pressure.normalization` block; no probability/availability/binding changes |
| `scripts/demo_real_observation.py` | Thin CLI connecting existing collector, loader, normalizer and Bayesian update |
| `tests/test_catalogue_loader.py` | File/schema/reference/numeric/cost/usage-policy tests |
| `tests/test_normalizer.py` | CPU thresholds, malformed/missing/stale/nonfinite evidence and optional chart errors |
| `tests/test_real_observation_integration.py` | Real-format mocked HTTP through the actual collector and Bayesian engine; CLI success/failure tests |
| `docs/PHASE1_OBSERVATION_INTEGRATION.md` | Interfaces, reproducible commands, results and limitations |

No changes were made to `core/models.py`, `diagnosis/bayesian.py`, `optimization/planner.py`, or `core/orchestrator.py`. SHA256 checks also confirmed `collectors/base.py`, `collectors/netdata.py`, `collectors/docker.py`, and `core/collection_orchestrator.py` are byte-for-byte unchanged from task start. Existing tests were not modified. No frontend, twin, API, energy meter or CollectorManager was implemented. No commits or branch operations were performed.

The starting worktree already had a whitespace-only modification on line 1 of `collectors/netdata.py`; it is preserved. Consequently a whole-worktree `git diff --check` reports that pre-existing trailing whitespace. Do not attribute it to this integration.

## Loader interface

```python
load_catalogues(
    directory: str | Path = DEFAULT_CATALOGUE_DIR,
    *, research_mode: bool = False,
) -> LoadedCatalogues
```

`DEFAULT_CATALOGUE_DIR` is resolved relative to the module, so file lookup does not depend on the caller's working directory. `LoadedCatalogues` contains:

- `hypotheses: tuple[Hypothesis, ...]`
- `initial_prior: dict[str, float]`
- `tests: dict[str, DiagnosticTest]`
- `test_metadata: dict[str, dict[str, Any]]`
- `metadata: dict[str, Any]` with complete parsed `knowledge`, `tests`, and `model` documents, including versions, IDs, status, source information, usage policy, dataset/count placeholders and assumptions
- `research_mode: bool`

The companion structures retain all provider bindings, availability, provenance, target selectors, evidence groups and normalization configuration without adding fields to protected mathematical models. Their dictionaries remain mutable, as do the existing mathematical tables; callers must not mutate a loaded model during inference.

The YAML parser subclasses `yaml.SafeLoader` and rejects duplicate mapping keys instead of silently overwriting identifiers. JSON uses `json.loads` with duplicate-key detection. Supported schema version is integer 1; nonempty versions and symbolic IDs are required. The loader validates all prior, likelihood, relationship test/hypothesis and outcome references, normal outcomes, complete hypothesis coverage, numeric values, costs, freshness and live-binding gates. All three files must be present and valid; missing data is not filled with invented defaults. Errors surface as `CatalogueError(ValueError)` with context.

Final numerical validation reuses `validate_distribution()` and `DiagnosticTest.__post_init__()`, including the engine's `1e-9` probability-sum tolerance. Catalogue milliseconds are divided by 1000; e.g. the CPU test's 45 ms becomes .045 s. The three catalogue files produce 15 Hypothesis objects, 20 DiagnosticTest objects and 300 complete likelihood distributions. Qualitative support/contradiction labels never become numerical probabilities.

### Research policy

Call `load_catalogues(research_mode=True)` explicitly. Without this flag the loader raises `PermissionError`. This phase deliberately does not support production inference, even if a future input document claims calibration. The model must permit simulation/research use. The CLI additionally checks CPU test simulation availability.

Loading constructs numerical models; it does not authorize or execute catalogue operations. All 20 existing `availability.live` flags remain false, every live binding remains null, and the model's `usage_policy.live_diagnosis` remains false. The CLI only acquires the already-supported collector snapshot for a controlled research demonstration. It does not dispatch any catalogue `logical_operation`, activate a Netdata Function or enable unverified diagnostics.

## Normalizer interface

```python
normalize_cpu_observation(
    snapshot: Any,
    test: DiagnosticTest,
    test_metadata: dict[str, Any],
    target_identifier: str,
    *,
    retrieved_at: datetime | None = None,
    duration_seconds: float = 0.0,
) -> NormalizedObservation
```

The result contains `observation: Observation` and `metadata: dict[str, Any]`. Only `host_cpu_pressure` with outcomes `normal`, `high`, `critical` is supported. Unsupported tests/configuration raise `ValueError`; invalid provider evidence becomes FAILED or UNAVAILABLE without a diagnostic outcome.

The input is the actual existing collector's snapshot, particularly:

```python
{
    'source': 'netdata',
    'cpu': {
        'time': ...,  # actual Netdata row timestamp, Unix seconds
        'guest_nice': ..., 'guest': ..., 'steal': ...,
        'softirq': ..., 'irq': ..., 'user': ...,
        'system': ..., 'nice': ..., 'iowait': ...,
    },
    'errors': {...},
    # Other collector fields are allowed and not used as CPU evidence.
}
```

### CPU metric definition

The normalizer uses exactly these configured **Netdata chart percentages**:

```text
activity_percent = user + system + nice + irq + softirq + steal + guest + guest_nice
```

Iowait and idle are excluded from activity; iowait is retained separately in metadata when present. This is a host-wide percentage, not a Docker per-container CPU percentage (which can exceed 100 across cores).

This is not a blind sum of raw Linux counters. Netdata's Linux proc.plugin subtracts guest from user and guest_nice from nice before publishing separate dimensions. Thus its chart dimensions are disjoint for this calculation. The normalizer checks their exact names, presence, finite numeric type, individual range and aggregate bounds, and refuses unknown dimensions. It never uses the collector's missing-value-as-zero derived aggregate as a fallback.

Sources: [Netdata proc.plugin implementation](https://github.com/netdata/netdata/blob/master/src/collectors/proc.plugin/proc_stat.c) and [official system-statistics metric definitions](https://www.netdata.cloud/integrations/data-collection/operating-systems/system-statistics-procplugin-procstat/). Repository `netdata_charts.json` identifies `system.cpu` as Linux `/proc/stat`, units `percentage`, with the expected dimensions. Upstream semantics were inspected; this task did not pin or inspect the locally installed Agent's executable source/version. Other OS collectors and arbitrary `/proc/stat` input are unsupported.

Thresholds live only in `host_cpu_pressure.normalization.thresholds`:

| Activity | Outcome |
|---|---|
| below 70% | `normal` |
| 70% inclusive to below 90% | `high` |
| 90% inclusive and above | `critical` |

They are marked `provisional_research_thresholds_not_calibrated`. These categories do not establish that CPU load caused a Voting App incident, and the synthetic likelihood table was not fitted to this exact window or threshold protocol.

### Sampling window and timestamps

The existing collector requests `GET /api/v1/data` with `chart=system.cpu`, `after=-5`, `points=1`, timeout 5 seconds. It supplies no explicit grouping argument; the catalogue records `group: agent_default`. The collector returns the first aggregate data row as a label/value mapping. It does not preserve the API's actual window start/end, grouping metadata or units. The normalizer therefore records the **requested** five-second/one-point window separately and sets `actual_window=None`, with an explicit limitation. It does not claim an exact instantaneous sample or invent actual interval bounds.

For completed evidence, Observation.timestamp is the real returned CPU row timestamp, converted from Unix seconds to UTC. Retrieval/processing time is recorded separately from actual wall-clock time (or a caller-supplied aware datetime in deterministic tests). The existing 15-second catalogue freshness limit is enforced relative to retrieval time; its exact boundary is accepted. Future sample timestamps are unavailable, not silently clamped.

If sampling time is absent, the CPU value may be retained for inspection, but no Bayesian evidence is admitted because freshness cannot be established. For any FAILED/UNAVAILABLE result, Observation.timestamp records the actual normalization attempt, and companion metadata states `observation_timestamp_role=processing_attempt`. A missing sampling time remains null in metadata; it is never replaced with retrieval time and presented as a real measurement time.

The CLI measures total `collect()` duration with `perf_counter`; because collection includes other metrics, that duration is not a CPU-only acquisition latency. `measured_energy_joules` is always None; no meter or random value is used.

### Error mapping

| Condition | Status and behavior |
|---|---|
| Complete, fresh, valid CPU row | COMPLETED plus `normal/high/critical` |
| Missing CPU row/dimension or explicit no-data | UNAVAILABLE, no outcome |
| CPU chart 404 | UNAVAILABLE, no outcome |
| CPU HTTP/provider failure | FAILED, no outcome |
| Malformed snapshot/errors/CPU shape or nonfinite/invalid values | FAILED, no outcome |
| Missing, stale or future measurement timestamp | UNAVAILABLE, no outcome |
| Malformed/out-of-range timestamp | FAILED, no outcome |
| Optional network chart 404 with valid CPU | CPU remains COMPLETED; errors retained separately |

The existing collector represents errors as strings; classification of no-data/404 uses its current messages. Structured HTTP error metadata would be preferable in a later backwards-compatible collector extension. A metric acquisition failure never becomes `critical` or another fault outcome. No absent dimension becomes zero.

## Bayesian consumption

The CLI looks up the exact test object `loaded.tests['host_cpu_pressure']` and calls:

```python
posterior = apply_observation(
    loaded.initial_prior, test, normalized.observation,
)
validate_distribution(posterior, 'posterior')
```

This is the existing protected implementation, not a second Bayes formula. The engine rejects mismatched test identifiers and unsupported completed outcomes. FAILED/UNAVAILABLE observations leave the prior unchanged. Tests independently check the posterior against the selected catalogue row and verify normalization.

## Running the demonstration

Use the existing virtual environment from the repository root:

```bash
.venv/bin/python scripts/demo_real_observation.py
.venv/bin/python scripts/demo_real_observation.py --base-url http://127.0.0.1:19999 --target linux-host-01
```

With the environment activated, the requested `python scripts/demo_real_observation.py` command works as well. The script establishes the repository import path for direct script execution. `--target` is an explicit user-supplied label; it is not claimed to be a discovered Netdata node identity.

Output JSON contains connection/evidence status, real CPU activity, measurement/retrieval timestamps, threshold/requested-window information, collector errors, observation/status, model identity/version/calibration status, catalogue versions, prior, posterior, sum and success/failure text. It always identifies its mode as **research/bootstrap inference, not a calibrated diagnosis**. Return code is 0 only for a completed evidence update; unavailable Agent/data/configuration returns 1 with an explanation, never a synthetic fallback.

The public collector API only exposes full `collect()` with no category filter, so it also acquires RAM/swap/network metrics. No new HTTP client, duplicate collector, private-method collection wrapper or collector rewrite was introduced. Existing optional-chart error handling already lets CPU collection succeed despite network 404s.

## Dependencies

Current `requirements.txt` already declares all direct dependencies: pytest>=8.0,<10, requests>=2.31,<3, docker>=7,<8, PyYAML>=6,<7. The existing environment already contained pytest 9.2.0, requests 2.34.2, docker 7.2.0, PyYAML 6.0.3. No installation or requirements modification was needed.

## Verification performed

Only explicit test paths were run. Root-level collector smoke scripts were not discovered or imported by pytest.

| Command/check | Actual result |
|---|---|
| `.venv/bin/python -m pytest -q tests/test_engine.py tests/test_validation.py` | **65 passed in 5.07s** |
| `.venv/bin/python -m pytest -q tests/test_catalogue_loader.py tests/test_normalizer.py tests/test_real_observation_integration.py` | **85 passed in 25.46s** |
| `.venv/bin/python scripts/validate_catalogues.py` | PASS: 15 hypotheses, 20 tests, 19 relationship groups, 300 complete synthetic distributions; live-disabled warning retained |
| `.venv/bin/python scripts/demo_real_observation.py` against local Agent | Exit 0, real CPU observation completed and Bayesian posterior updated |
| SHA256 comparison of four protected modules and four collector/coordinator files | All unchanged from task start |
| Whole-worktree `git diff --check` | Reports pre-existing trailing whitespace at `backend/collectors/netdata.py:1`; preserved unchanged |

An earlier focused run reported **2 failed, 83 passed**: PermissionError (an OSError subclass) was inadvertently wrapped as CatalogueError by the new loader's broad parse-error handler. The integration code was corrected to preserve permission exceptions, and the full focused suite then passed. No protected files or original tests were changed to fix it.

### Actual live observation

The final live run used the existing collector against `http://127.0.0.1:19999`:

```text
CPU activity:             34.5285634 percent
Measurement timestamp:    2026-11-14 20:29:15+00:00
Test:                     host_cpu_pressure
Execution status:         completed
Outcome:                  normal
Model:                    voting-app-synthetic-bootstrap / 0.1.0
Model status:             synthetic_provisional_not_calibrated
Posterior sum:            1.0
host_cpu_saturation prior: 0.059405941
host_cpu_saturation after: 0.003798430024583827
```

The snapshot also reported HTTP 404 for `net_speed.docker0` and `net_duplex.docker0`, without invalidating CPU evidence. The complete run JSON was captured in `/tmp/infrasage-phase1-live.json` for inspection; it is a temporary execution artifact, not a committed fixture or calibrated dataset. Future runs naturally produce different measurements.

The sandbox initially prevented local sockets (`Operation not permitted`). The same collector and final CLI were subsequently run with approved access outside the network sandbox. The successful live result is from the actual Agent, not mocked-test output. Unit/integration tests themselves use deterministic mock HTTP responses and do not require the Agent.

## Limitations and next integration task

This proves the real observation-to-Bayesian boundary only. A single CPU observation with synthetic priors/likelihoods does not diagnose an incident. Catalogue test bindings remain unverified and disabled; initial alert detection, fresh diagnostic execution, model calibration, repeated evidence handling and energy measurement remain outside scope. No dynamic planner execution was implemented.

Next, connect the existing planner's selected test to the collection layer without adding a second decision owner:

1. Review `core/collection_orchestrator.py::CollectionOrchestrator`: it already provides `collect_initial`, backwards-compatible `collect_all`, and supplementary `collect_on_demand`. Reuse its collection/routing work when introducing the team's lightweight CollectorManager contract; do not build a duplicate collection system.
2. Define selected-test execution as `(DiagnosticTest, target, companion execution metadata) → normalized Observation plus provenance`. Bind only a small verified operation set, with explicit capabilities, timeouts and failure/unavailable statuses. A provider name/logical operation alone is insufficient authorization.
3. Filter candidate tests using live binding/capability, target, freshness, evidence groups and consumed observations before calling the unchanged `DiagnosticPolicy.select`. Retain budget/stopping ownership in the single investigation layer.
4. Have the approved integration boundary route `selection.test.identifier` through that registry, normalize its actual result, and call existing `apply_observation`. Do not change simulation/replay behavior or the protected lifecycle without a separate authorization for the next phase.
5. Add deterministic selected-ID dispatch, failed/unavailable/no-update, duplicate-evidence and budget checks, followed by one opt-in real collector execution. Keep the research/calibration distinction visible throughout.
