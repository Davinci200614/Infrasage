# Pipeline readiness assessment — 15 November 2026

The pipeline starts and collects real evidence, but it is **not ready for reliable
automatic root-cause diagnosis**. These tests distinguish successful execution,
observed faults, and correct diagnosis. A successful shell exit is not proof of a
correct diagnosis.

## What was tested

Four controlled scenarios ran through the public `./infrasage.sh investigate`
command against the manifest-owned Docker Voting App. Each scenario started with
a verified vote reaching PostgreSQL. The harness injected the fault, captured
container state and diagnostic output, restored Redis/network/worker state, and
verified another vote reached PostgreSQL. No unrelated containers were targeted.

| Scenario | Collected evidence | Final decision | Assessment |
| --- | --- | --- | --- |
| Healthy | Redis running; worker TCP connected | ABSTAIN | Both supported checks passed; no explicit healthy verdict exists |
| Redis stopped | Redis stopped; TCP skipped because Redis has no current IP | ABSTAIN | Outage observed; Redis stopped leads at 53.2%, below the 95% threshold |
| Redis TCP listener disabled | Redis running; worker TCP refused | ABSTAIN | Failure observed; wrong endpoint leads at 42.3%, despite the known disabled listener |
| Worker disconnected from Redis network | Redis running; helper reports OS error 101, network unreachable | ABSTAIN | Network symptom is discarded as execution failure; only runtime evidence updates beliefs |

All four application restorations passed functional voting checks. None of the
three injected faults produced a root-cause diagnosis. These are individual runs,
not accuracy estimates or evidence of reliability across environments.

## Fixes made during this assessment

- The public launcher previously exited with `Cannot resolve the owned Redis-tier
  endpoint` during the Redis stop scenario. It now requests runtime-only
  investigation when no current endpoint is available. It does not guess an IP,
  restart Redis, or turn a missing endpoint into completed TCP evidence.
- The experiment runner now resolves a helper image tag to its immutable local
  image ID and verifies its build labels before injecting a fault. Previously a tag
  silently made TCP probing ineligible. Restoration remains available without the
  helper image.
- Added `--via-launcher` to the fault harness. Engine-only experiments retain a
  pre-fault IP and therefore could not expose the public launcher's IP dependency.
- Added regression coverage for absent/ambiguous endpoints, image resolution,
  helper validation, launcher failures, and restoration without a helper.

## Improvements in priority order

1. **Show verified findings separately from root cause.** Print a short summary
   such as `Redis container is stopped` or `Worker connection to Redis was refused`,
   with the final diagnosis and skipped/failed checks. Keep `ABSTAIN` when initiating
   cause is uncertain. A proven stopped container is useful even without a cause
   classification. This summary is not yet implemented.
2. **Represent network failures as observations.** Add explicit outcomes for
   network/host unreachable through the helper, collector, adapter, test catalogue,
   likelihood tables, and tests. Currently the TCP vocabulary is only connected,
   refused, and timeout. Preserve DNS errors, permissions errors, helper failures,
   and timeouts as distinct events; never convert all execution failures into
   evidence against Redis. Rebuild the source-validated helper after changes.
3. **Add discriminating live probes.** Implement worker runtime state, Redis PING
   or a bounded service check, owned listener/configuration checks, and worker
   network attachment/endpoint validation. Add PostgreSQL and vote-path checks to
   diagnosis execution. Presently the functional vote is a lab baseline/restoration
   check, not evidence used by the planner. Running Redis plus TCP refusal cannot
   distinguish disabled service, wrong port, and other causes by itself.
4. **Revise the model's scope.** The current prior covers 15 fault hypotheses and
   has no explicit healthy state. Healthy checks redistribute probability toward
   unchecked failures. Model healthy operation and unknown faults deliberately,
   and handle cascading failures: Redis loss can also stop the worker. Preserve
   uncertainty about the initiating cause rather than assuming every symptom is
   an independent single failure.
5. **Calibrate with repeated labeled experiments.** Collect repeated fault and
   healthy runs with varied startup timing, failure duration, and environment.
   Estimate and version likelihoods; evaluate on held-out runs. Measure false
   alarms, correct diagnoses, abstentions, probe failures, and time to detection.
   Do not lower the 95% threshold merely to make these examples produce a label.
   The current probabilities and energy estimates are provisional.
6. **Add an operational lifecycle.** Continuous checks, incident triggers,
   timestamped persisted investigation traces, bounded retry policy, service
   shutdown, and usable status summaries are needed for ongoing operation. Keep
   duplicate/retried evidence from being counted twice. API/dashboard files remain
   placeholders; their implementation is separate work.

## Readiness gates

- A stopped Redis container is reported clearly even with no IP or TCP helper.
- Healthy operation produces a scoped health summary without implying that every
  untested component is healthy.
- Stopped Redis, disabled listener, wrong endpoint, and worker network loss have
  distinguishable evidence and expected integration-test outcomes.
- Every test verifies restoration and halts fault injection if restoration fails.
- Known probe failure modes appear explicitly in output rather than being treated
  as successful negative evidence.
- Repeated held-out experiments meet agreed diagnosis/false-alarm/latency targets;
  no such production targets or validation currently exist.

## Reproduce

Run only on the dedicated Voting App lab. Fault scenarios temporarily interrupt it.

```bash
./infrasage.sh setup
./infrasage.sh start
.venv/bin/python scripts/run_voting_app_experiments.py \
  --helper-image infrasage-tcp-probe:phase2b1 \
  --scenario all --allow-fault-injection --via-launcher \
  --output experiments/data/readiness-rerun
./infrasage.sh status
```

Emergency restoration of the ownership-checked lab, if a test is interrupted:

```bash
.venv/bin/python scripts/run_voting_app_experiments.py \
  --helper-image infrasage-tcp-probe:phase2b1 \
  --restore-only --allow-fault-injection
```

## Evidence

The final runs include raw launcher output, admitted/rejected evidence, posterior
history, verified injected state, and functional restoration:

- [Healthy](../experiments/data/readiness-2026-11-15/launcher-after/048a2aaebf6349b3a56870098aff25de-healthy.json)
- [Stopped Redis](../experiments/data/readiness-2026-11-15/launcher-after/6f532bb337304130a1e5ba697a38aa28-redis_stopped.json)
- [Disabled Redis listener](../experiments/data/readiness-2026-11-15/launcher-after/700621927c59498c844aed3e1a1e9dde-redis_port_unavailable.json)
- [Worker network interruption](../experiments/data/readiness-2026-11-15/launcher-after/405f6c5599414446a75c63a6bfe17689-worker_network_interruption.json)
- [Original public-launcher Redis-stop failure](../experiments/data/readiness-2026-11-15/launcher-before/cb65abe6b5864c13bd3bda47008e6698-redis_stopped.json)

The four initial engine-only artifacts in the parent evidence directory used an
image tag before the runner fix and skipped connectivity. They are retained for
audit and excluded from the final scenario results above.
