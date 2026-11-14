# Phase 2B.1 — Worker-to-Redis TCP connectivity

Implemented on the existing Phase 2A codebase, starting from HEAD `b7fe181` on 2026-11-14. No branch switch, commit, merge, push or dependency installation was performed.

## Result and scope

Both diagnostic IDs now execute through the existing manager and adapter:

- `redis_runtime_state`: unchanged targeted Docker inspection.
- `worker_redis_connectivity`: a bounded, disposable helper sharing the explicitly identified worker's network namespace.

The existing catalogue loader, mathematical dataclasses, Bayesian engine, planner, investigation orchestrator, CPU normalizer and Netdata collector are unchanged. The two catalogues inspected for this phase and their synthetic likelihood parameters are unchanged. All catalogue live flags remain false; only individually verified, process-local bindings become executable.

Real isolated-lab checks produced connected, refused and timeout observations from the worker context. DNS failure produced FAILED with no outcome, and a stopped worker produced UNAVAILABLE. Both previous runtime-state cases and the Phase 1 Netdata demonstration were rerun successfully. See the verification section for exact evidence and limitations.

## Files

| File | Purpose/change |
|---|---|
| `backend/collectors/docker.py` | Additive worker TCP probe method and optional initialization timeout; existing APIs/default behavior retained |
| `backend/collectors/tcp_probe.py` | Parameter validation, controlled image verification, bounded helper lifecycle, result validation and cleanup |
| `backend/collectors/tcp_probe_helper.py` | Fixed Python DNS/TCP implementation executed inside the helper |
| `backend/core/collector_manager.py` | Additive verification/routing for one worker/endpoint/image-specific binding |
| `backend/core/diagnostic_adapter.py` | Connectivity mappings and additional endpoint/network-namespace checks using existing strict observation validation |
| `lab/tcp-probe/Dockerfile` | Controlled local helper image definition |
| `scripts/build_tcp_probe_image.py` | Build helper from a two-file archive context; repository secrets and other files are not sent to Docker |
| `scripts/demo_worker_redis_connectivity.py` | Explicit worker/endpoint CLI without planning or investigation orchestration |
| `tests/test_worker_redis_connectivity.py` | Deterministic provider, socket, routing, validation, timeout and cleanup tests |
| `docs/PHASE2B1_WORKER_REDIS_CONNECTIVITY.md` | This contract and verification record |

Pre-existing changes to `backend/collectors/netdata.py` and the untracked collector audit document were preserved byte-for-byte. Protected mathematical files, catalogue loader, CPU normalizer and catalogue files were checked using SHA256 hashes against task-start contents.

## Method: worker network context without modifying the worker

Docker's `container:<full worker ID>` network mode shares the worker container's network stack. The helper is created in that mode, and its inspected HostConfig.NetworkMode is checked before starting it. This is not a TCP attempt from the Linux host or an independently attached bridge container. The helper does not execute tools inside the worker, so the worker image need not contain Python, curl, ping or netcat.

Primary references: [Docker container networking](https://docs.docker.com/engine/network/#container-networks) and [Docker SDK container lifecycle/timeout interfaces](https://docker-py.readthedocs.io/en/stable/containers.html).

The operator explicitly configures:

- Exact worker container name or full 64-character container ID.
- Redis host/service name or IP address.
- TCP port.
- Connection timeout and overall execution deadline.
- Pinned local helper image ID, not a mutable tag.

The worker is inspected before and after the socket attempt. It must remain the same running container with the same canonical name. Container replacement, name/ID mismatch, missing targets and ID prefixes are rejected. A stopped worker cannot be probed.

### Controlled helper image

Build once, explicitly, from the repository root:

```bash
.venv/bin/python scripts/build_tcp_probe_image.py
```

The builder sends only the Dockerfile and helper Python source in its archive context. The Python base image may be downloaded during this separate build step. No image is pulled or built while executing a diagnostic. No tools are installed in the worker.

The output includes a full `sha256:...` helper image ID and the helper-source checksum. Execution requires a local image matching that ID and labels identifying protocol v1 and the current helper-source SHA256. Changing helper source requires rebuilding the image. Local image labels are an operator-controlled compatibility declaration, not cryptographic attestation against a hostile Docker daemon. The resulting image ID is pinned in each binding; the Dockerfile base tag itself is not a permanent reproducibility pin.

The container runs the fixed `/opt/infrasage-tcp-probe.py` entrypoint and receives only validated JSON arguments. It does not accept shell commands. It has no published ports, no host mounts, no privileged mode, all capabilities dropped, a read-only root filesystem, non-root UID/GID 65534, a PID limit and memory/CPU limits. Its log output is bounded. It sends no Redis command or application payload: it connects and closes a TCP socket.

## Exact interfaces

```python
# Existing collector, additive API:
DockerCollector(*, timeout=None)
DockerCollector.probe_worker_redis(
    self, *, container_id=None, container_name=None,
    redis_host, redis_port, connection_timeout=1.0,
    execution_deadline=8.0, helper_image, _deadline_at=None,
) -> dict

# Existing manager, additive verification API:
CollectorManager.verify_connectivity_binding(
    self, test, target, *, redis_host, redis_port,
    connection_timeout=1.0, execution_deadline=8.0, helper_image,
) -> dict

# Existing manager and adapter interfaces remain:
CollectorManager.execute_test(self, test, target) -> dict
DiagnosticAdapter.adapt(self, test, execution_result, target) -> Observation
DiagnosticAdapter.adapt_with_metadata(
    self, test, execution_result, target, *, retrieved_at=None,
) -> NormalizedObservation
```

`_deadline_at` is an internal monotonic deadline supplied by the manager so initial provider setup consumes the same execution budget. External callers normally omit it.

Supported limits are deliberately small: connection timeout .1–3 seconds, overall execution deadline 3–10 seconds, and at least two seconds between the socket timeout and overall deadline for lifecycle/cleanup work. The default overall deadline is eight seconds. Redis port must be an integer from 1 to 65535; hosts must be a valid IP or supported ASCII DNS-label sequence. Shell metacharacters, wildcard hosts, mutable image tags, nonfinite values and malformed identities are rejected before launch.

## Verification, availability and binding

Use `load_catalogues(research_mode=True)` and pass its result directly to the existing CollectorManager. No loader API change is required.

`verify_connectivity_binding()` accepts only `worker_redis_connectivity` with catalogue logical operation `worker_to_redis_tcp` and research availability. It performs a real probe using the requested worker, endpoint and helper build. Any valid socket outcome, including refused or timeout, verifies that the diagnostic protocol executed; it does not certify Redis health. Provider, DNS, malformed-result, deadline and cleanup failures do not activate a binding.

Successful verification creates a deep-copied, manager-local Docker execution definition. The original catalogue still says provider precision with live execution disabled. The local binding records:

```python
{
    'provider': 'docker',
    'operation': 'worker_to_redis_tcp',
    'container_id': '<canonical worker ID>',
    'container_name': '<exact worker name>',
    'docker_endpoint': '<actual SDK endpoint>',
    'redis_host': '<explicit host>',
    'redis_port': 6379,
    'connection_timeout': 1.0,
    'execution_deadline': 8.0,
    'helper_image': 'sha256:<full local image ID>',
    'verified_at': '<actual probe sample UTC timestamp>',
    'freshness_max_seconds': 15,
}
```

`execute_test()` performs a new probe against that exact configuration. It never changes endpoint/image from arbitrary caller fields. Conflicting endpoint or budget fields in the target are rejected. Runtime-state and connectivity bindings have distinct test/operation checks. All other catalogue diagnostics remain disabled. Nothing is written back to YAML or globally enabled.

Verification and execution each launch one helper and make one bounded socket attempt protocol. The CLI deliberately demonstrates these two stages, so it normally makes two probes. No evidence is sent to Bayesian inference or counted twice in this demonstration.

## Probe protocol and outcomes

The helper resolves the configured endpoint within its execution budget, then uses a shared connection-time budget across at most 16 address records. It attempts no other port or hostname. The fixed protocol reports a request nonce, worker ID, endpoint, status, sample timestamp and either one declared outcome or an execution error.

| Situation | Observation behavior |
|---|---|
| TCP connect succeeds | COMPLETED / connected |
| Address attempts explicitly return ECONNREFUSED | COMPLETED / refused |
| Socket connection budget expires or ETIMEDOUT | COMPLETED / timeout |
| DNS resolution failure | FAILED, no outcome |
| DNS/helper execution consumes overall deadline without a reliable socket result | FAILED, no outcome |
| Worker missing, not running, identity ambiguous, or binding invalid/expired | UNAVAILABLE, no outcome |
| Docker API failure, launch failure, malformed helper result, unavailable route/internal error | FAILED, no outcome |
| Missing/unrecognized local helper image | UNAVAILABLE before activation |
| Helper cleanup fails | FAILED, no outcome; helper identity and cleanup error retained |

A successful TCP connection proves endpoint reachability only. It does not prove Redis authentication, command processing, application health or a root cause. Refused and timeout are modeled connectivity outcomes; DNS and arbitrary internal errors are not silently mapped to those categories.

### Bounded execution and cleanup

A monotonic overall deadline covers the probe lifecycle, including worker inspection, local image lookup, helper create/start/wait/log retrieval and post-probe worker inspection. Initial provider negotiation is separately capped and shares the manager's absolute deadline. Each Docker request is limited to its remaining budget. Two seconds of the total deadline are reserved for cleanup; helper process execution also has an alarm covering DNS and socket work. Exceeding the active budget produces FAILED, not a fabricated socket timeout outcome.

The helper is removed in a finally block on success, invalid output, launch/wait errors and deadline expiration. If create times out, cleanup attempts a lookup using the unique helper name and validates its identifying label before removal. It never stops or removes the worker. Cleanup errors override a would-be successful result, preserve the initial execution error separately, and report helper ID/name for inspection.

As with any external Docker daemon, cleanup cannot be guaranteed if the daemon becomes unreachable or never acknowledges a request. The deadline is enforced through local monotonic checks and bounded SDK request timeouts, not a hard real-time operating-system guarantee. Failed cleanup is explicit rather than reported as success. The Docker client's prior request timeout is restored even on failure. A client must not be used concurrently during a probe because this timeout is temporarily adjusted; the existing manager is not a concurrent execution service.

## Raw evidence and Observation contract

The existing execution envelope is retained: source docker, test_id, lowercase execution status, canonical target dictionary and `docker:<worker ID>` identifier, data, observed_at, retrieved_at, duration, Docker endpoint and binding. Connectivity adds:

- `probe_config`: exact configured endpoint, budgets and helper image.
- `namespace`: `container:<worker ID>`.
- `helper_image`, helper ID/name and cleanup completion flag.
- `request_id` and complete `probe_result` protocol payload.
- `data.outcome` and `data.endpoint`, alongside the worker ID/name.
- Explicit cleanup/initial execution errors when relevant.

The provider validates the helper request nonce, worker ID, image, namespace, endpoint, exit status, output size and sample time before returning completed evidence. The manager and adapter check endpoint/namespace/configuration consistency against the verified binding. The adapter retains the strict Phase 2A test-ID, raw-data identity, target, source, timestamp-ordering and freshness checks. Connectivity cannot be relabeled as runtime state or another worker.

Completed Observation.timestamp is the helper's actual UTC socket-result timestamp. The original provider payload and timestamps remain in `NormalizedObservation.metadata['execution_result']`. Failed/unavailable observations use an honestly labeled processing-attempt time, retain original evidence/error metadata and have no outcome. Execution duration is measured; measured_energy_joules remains None. The existing mathematical Observation and Phase 1 NormalizedObservation envelope are reused unchanged.

The helper shares the worker's network namespace and Docker-provided network view, but not arbitrary application-mounted resolver files, client libraries, credentials or application-specific DNS/proxy configuration. This is a namespace TCP test, not an exact reproduction of the worker application's connection stack.

## Demonstration

Use the full helper image ID printed by the build command:

```bash
.venv/bin/python scripts/demo_worker_redis_connectivity.py \
  --worker-name my-worker \
  --redis-host redis --redis-port 6379 \
  --helper-image sha256:FULL_IMAGE_ID \
  --connection-timeout 1 --execution-deadline 8
```

`--worker-id` may replace `--worker-name`; the ID must be full-length. The worker and endpoint are explicit operator assignments, not automatically inferred topology. Output includes worker identity, Redis endpoint, execution/verification, observation, binding, source, timestamps, duration, model status and provenance. Exit 0 means a completed connectivity observation, including refused or timeout. FAILED/UNAVAILABLE exits 1 with no diagnostic outcome. No planner, investigation loop or automatic remediation is invoked.

The old demonstrations remain:

```bash
.venv/bin/python scripts/demo_real_observation.py
.venv/bin/python scripts/demo_live_diagnostic.py --container-name my-redis
```

## Automated tests

All test invocations use explicit test paths; root-level live collector smoke scripts are not discovered.

The 61 new deterministic cases cover the three socket outcomes, helper socket implementation, DNS and execution-deadline errors, unavailable worker, wrong identity, local helper image validation, disabled/expired bindings, endpoint pinning, malformed results, missing timestamps, Docker errors, active-deadline enforcement, cleanup after failures and preservation of original execution errors when cleanup also fails.

The first combined run passed **290 tests in 38.38s** (231 existing plus the initial 59 new cases). Two additional deadline/cleanup cases were then added; the focused suite passed **61 tests in 2.33s**. **Final combined run: 292 passed in 34.98s — all 231 existing tests plus 61 new tests.**

```bash
.venv/bin/python -m pytest -q \
  tests/test_engine.py tests/test_validation.py \
  tests/test_catalogue_loader.py tests/test_normalizer.py \
  tests/test_real_observation_integration.py \
  tests/test_collector_manager.py tests/test_diagnostic_adapter.py \
  tests/test_worker_redis_connectivity.py
.venv/bin/python scripts/validate_catalogues.py
```

Catalogue validation passed: 15 hypotheses, 20 tests, 19 relationship groups and 300 complete synthetic distributions; the live-disabled warning remains correct for the catalogue files. No dependencies were added or installed.

## Actual live verification

With approved access outside the socket sandbox, the controlled local helper image was built and an isolated internal Docker bridge created. Two disposable containers were launched from the cached Redis image: one actual Redis server, and one lab worker network anchor running a sleep process. The diagnostic never executed a tool in that worker. Neither container published ports or used application data volumes.

This is a real worker-container network-context demonstration, not a claim of testing the deployed Voting App worker or actual workload behavior. The disposable Redis server had persistence disabled.

| Live case | Actual result |
|---|---|
| Redis server, port 6379 | COMPLETED / connected |
| Same server, intentionally closed port 6380 | COMPLETED / refused |
| Unassigned address on the isolated bridge, port 6379, .4-second connection timeout | COMPLETED / timeout |
| `missing.invalid` hostname | FAILED, no outcome; DNS did not produce a reliable socket result within the active Docker/helper deadline |
| Worker stopped | UNAVAILABLE, no outcome |
| Redis runtime while running | COMPLETED / running |
| Redis runtime after stopping | COMPLETED / stopped |
| Phase 1 local Netdata CPU demonstration | Success: real CPU 7.8340502%, normal outcome, posterior sum 1.0 |

The live invalid-hostname check reached the parent's bounded wait deadline and was reported as execution failure; it is not claimed as a successfully captured NXDOMAIN response. Deterministic tests separately verify explicit DNS-resolution error handling.

```text
Worker ID: f956dec4ec6660b1bbf7e1d56501cef39611ebb4f1d95f7f386df38a04340235
Redis ID: cce486d963abfa8d649b9639ee884d51d5696f252f888ae4266e4a2161358813
Helper image: sha256:167c897bcbb9bc26d8705493db5aec85c1c0d87292298bab035708ffd03e3f32
Helper source SHA256: 81cdd090a4a89f6ea42ff2274f531787c38e456998a2917e25d6039a4025bd63
Connected sample: 2026-11-14 22:23:35.881541+00:00
Refused sample:   2026-11-14 22:23:47.181652+00:00
Timeout sample:   2026-11-14 22:24:00.159202+00:00
```

Every launched probe helper was removed, including the invalid-hostname deadline case. The disposable worker, Redis and network were removed in the lab driver's finally block. Existing containers were not modified. The built helper/Python base images remain cached locally. Temporary raw verification records are `/tmp/infrasage-phase2b1-live.json`, `/tmp/infrasage-phase2b1-helper.json` and `/tmp/infrasage-phase2b1-netdata.json`; they are not committed datasets or calibrated model evidence.

## Remaining work

No autonomous planner loop is connected. The next phase should filter candidate diagnostics by verified capability, current target, freshness, consumed evidence and evidence groups, then route the existing planner's selection through the manager and adapter under the single investigation lifecycle. Preserve the existing simulation/replay behavior and research/calibration restrictions.

The probability catalogue remains synthetic. Establishing two executable diagnostics validates integration contracts; it does not establish operational root-cause accuracy, calibrated posterior confidence or measured energy savings.
