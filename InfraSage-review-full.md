# InfraSage review: feat/twin-phase-2

Branch: Davinci200614/Infrasage, feat/twin-phase-2 (head 11988a1). Reviewed on Sunday, November 15, 2026.

Severity: High means wrong or missing experimental data, or a check that reports a pass it has not earned. Medium means a weakened design or claim, or a broken secondary path. Low means hygiene, documentation, or cosmetic.

## Summary

The digital twin is well structured. Ground-truth isolation is enforced by an AST test, and the run lifecycle is clear. The unit suite passes (32 tests), but it does not exercise most of the problems below.

Three things matter most. Calibration data can be contaminated, so the likelihood tables may not mean what they say. The diagnosis runs on hand-set synthetic likelihoods that the project itself labels uncalibrated. And votes can be lost during the very faults the twin injects, and the result page does not recover after a database fault, so the baseline can report a healthy lab that is not.

## Priority order

1. Fix the experiment-data bugs (section 1) and re-run calibration, since they change data already collected.
2. Decide what the diagnosis may claim (section 3) and label results accordingly until calibration is wired in.
3. Fix the lost-vote and result-recovery bugs (section 7), then re-run fault experiments.
4. Make the validation checks able to fail (section 2).
5. Address the Docker socket exposure (section 8) before the lab setup is shared.
6. Energy and collector fixes (sections 5 and 6), then CI and hygiene (section 9).

## 1. Experiment data

E1 (High). The run seed is ignored for the workload. experiment.run() passes seed=int(WORKLOAD["seed"]) to the workload instead of its seed argument, so every run sends the same vote sequence.

E2 (High). Default runs all land in the evaluation split. workloads.yaml sets seed 421, which is odd, and splits.assign() maps odd seeds to evaluation. Calibration only receives data when someone passes an even seed by hand.

E3 (High). Validation runs leak into calibration. validation.py persists records to experiments/runs/, which aggregate.load_runs() reads. Validation runs with even seeds (42, 420, 422) are counted as calibration evidence.

E4 (High). An unreachable lab is recorded as a valid run. If a container cannot be resolved, investigate() returns abstain instead of raising. The runner writes lab_status: valid and the aggregator counts it.

E5 (High). The run schema rejects every real record. The runner always writes baseline_energy, but the schema sets additionalProperties to false and does not define that property. A record shaped like the runner's output fails validation with "Additional properties are not allowed ('baseline_energy' was unexpected)". So the claim in STATUS.md that 17 records are valid cannot hold for the current runner. Nothing in the repo runs the schema against run records.

E6 (High). Likelihood smoothing is wrong. In compute_likelihood_tables(), K is the number of outcomes observed in a cell, not the full outcome space. An outcome never seen in calibration gets no entry, so a rare but possible outcome gets zero probability.

E7 (Medium). Per-vote results are discarded. workload.run() records ok and error for each vote, but the run record keeps only len(votes), which counts failed attempts, under the name votes_submitted. The workload also runs only during warm-up, before injection, so the record has no evidence of the symptom.

E8 (Medium). Records and paths are fragile. _next_run_id() and _persist() do not guard against an existing file, so a re-used run_id overwrites a record. aggregate.py uses paths relative to the working directory.

Fix notes: E1 and E2 change which runs exist, so re-run calibration afterwards. For E3, write validation output to its own directory or filter it in load_runs(). For E5, add baseline_energy to the schema or remove it from the record, and add a test that validates a real record. For E6, use the full outcome set from the catalogue.

## 2. Validation and repair

V1 (High). Several checks cannot fail. check_reproducibility uses warm-up 0.0, so votes_submitted is always 0 and the baseline range is always 0. check_fault_symptom_coupling counts any diagnostic test as symptom evidence and never checks the symptom. check_isolation hardcodes label_in_logs = False, and loopback_only passes if the string 127.0.0.1 appears anywhere in the compose files. check_seed_determinism compares voter IDs built from the seed and index, so it passes by construction, and it sends real votes to the live target.

V2 (High). Chaos injection verification is unsound. _apply_chaos sends SIGSTOP, runs docker exec worker true, and reports "verified" when the exec fails. An exec failure does not show the process is stopped. Read the process state from /proc instead.

V3 (High). The chaos repair always reports restored. The other_unknown repair verifies with service_running, which reads State.Running. A SIGSTOPped container still reports running.

V4 (Medium). Validation results are never saved. run_all_validations() never calls _persist(), so nothing reaches experiments/validation/, although STATUS.md says it does.

V5 (Medium). The DNS checks hardcode the subnet 172.18.0. and include a redundant 172.18.0.3 test. Compose can assign a different subnet.

V6 (Medium). validate_repair() calls resolve_container() outside its try block, so a missing container raises instead of returning a repair_failed record.

Fix notes: give each check a known-bad input that it must reject, with a test. Run the validations after the experiment-data fixes, since several of them run full experiments.

## 3. Diagnosis and catalogues

D1 (High). Calibration never reaches the diagnosis. aggregate.py writes likelihoods.json, and nothing reads it. The diagnosis loads probabilities.json, whose header says "synthetic_provisional_not_calibrated", "live_diagnosis": false, and "Not empirically estimated". The loader checks that flag's type but never enforces its value. The Phase 6 calibration output therefore has no effect on any diagnosis, and the evaluation measures a hand-set model.

D2 (High). The live diagnosis cannot see the database. run_live_investigation() supports three tests: redis_runtime_state, worker_redis_connectivity, and worker_runtime_state. Every postgres test in tests.yaml is live: false, so the postgres_process_stopped hypothesis has no evidence path, and a postgres_unavailable run will abstain or be misattributed.

D3 (Medium). Some causes cannot be separated. Redis stopped, a worker-to-Redis network failure, and a wrong Redis endpoint all look like a failed connectivity test. Telling them apart depends on redis_runtime_state, which is live: false in the catalogue and only becomes live after a Docker verification step. No test checks that separation.

D4 (Medium). The catalogue asserts verification that did not happen. tests.yaml marks worker_runtime_state as verified with container_id "placeholder" and verified_at 2026-01-01, and redis_runtime_state as verified with a null binding. The runtime path replaces these, but the file states a verification that was never performed, and the loader accepts it.

Fix notes: D1 is a decision. Either wire calibrated likelihoods into the diagnosis and gate live use on calibration, or label every result as synthetic until then, and enforce the live_diagnosis flag in code. D2 needs database collectors or an explicit statement that database faults are out of scope. D3 needs a test that distinguishes the three causes using evidence the system can collect.

## 4. Live orchestrator and diagnostic adapter

O1 (Medium). The energy budget uses synthetic costs. run_live_investigation() checks the budget with expected_energy_joules, which comes from the illustrative synthetic_cost_for_planner_simulation values in tests.yaml (0.1 J and 0.02 J). Measured energy is attached to observations but never affects a decision. With the default limit of 100 J the budget can never bind, so the energy-aware policy is not driven by measurement.

O2 (Medium). Every selected test is consumed on any outcome. account() marks a test consumed whether it succeeded, failed, or was unavailable. A transient collector error removes that evidence for the rest of the investigation and often produces an abstention. This matches the "attempted at most once" rule, but the abstention reason should say evidence was lost to an error.

O3 (Medium). Unknown Docker states become unusable evidence. DiagnosticAdapter maps only running, exited, and restarting. A container in paused, created, dead, or removing state resolves to no outcome, so a dead or paused worker gets no diagnostic weight.

O4 (Medium). Admission and the Bayesian update are separate. validate_evidence() checks identity, freshness, and group uniqueness, then apply_observation() runs. If the Bayesian engine rejects an observation, the test is still consumed, and the trace does not distinguish rejection from consumption.

O5 (Low). The freshness limit is duplicated in three places: the adapter (15 seconds), the admission window (0 to 15 seconds), and runtime binding freshness (15 seconds). Changing one silently changes what is admitted. They should share one constant.

O6 (Low). Rejections for initial evidence do not say which field differed. "evidence identity or vocabulary mismatch" makes provenance failures hard to debug.

## 5. Energy measurement

N1 (High). The energy baseline always falls back to an estimate. measure_baseline() POSTs to /measure/interval when RAPL is reported as available. The server the runner starts, backend/energy/meter.py, has only /measure/start and /measure/stop. The 404 is swallowed and a CPU estimate is recorded, even on a machine where RAPL works.

N2 (High). The fallback estimate is wrong. When the server fails, stop_measurement() samples CPU for 0.1 seconds and multiplies by the full elapsed time. A brief spike is extrapolated across the interval, and the result is labelled "estimated" but is essentially noise.

N3 (Medium). The energy server has no concurrency control. _handle_measure_start() does not check for an active measurement, so a second start overwrites the first meter and leaks its temp file. The threaded handlers share state without a lock.

N4 (Medium). Error bodies are not valid JSON when the message contains a quote. _error() does not escape the message. The client then falls back to the estimate and hides the real error.

N5 (Medium). Two energy servers exist. backend/energy/meter.py is the one that runs. backend/twin/energy_server.py is an older copy with a different endpoint set, including /measure/interval. Remove the unused one.

Fix notes: N1 and N2 change the energy numbers in existing records, so mark those values invalid until re-measured. Check RAPL permissions explicitly and report failure rather than falling back silently.

## 6. Collectors, probe, and collector manager

K1 (High). The connectivity probe may not see the DNS fault. The probe runs in a helper container with network_mode "container:<worker>". That shares the network namespace but not /etc/hosts. The dns_override fault edits the worker's /etc/hosts, so the helper resolves Redis through its own resolver and can report "connected" while the worker cannot reach Redis. This follows from Docker's documented behaviour and needs a live run to confirm.

K2 (Medium). One stats failure aborts the whole Docker collection. DockerCollector.collect() reads stats fields without guards and catches only APIError, so a KeyError or TypeError from one container discards results for all containers.

K3 (Medium). CPU percent is wrong on some hosts. calculate_cpu_percent() defaults online_cpus to 1 when Docker omits it, which overstates usage on multi-core hosts. Use the length of percpu_usage as a fallback.

K4 (Medium). Container stats are collected serially, with about a second per running container, so collection time grows linearly.

K5 (Medium). The probe's outcomes are coarse. A completed TCP handshake counts as "connected", and the Redis protocol is never checked. A Redis that accepts connections but does not answer reads as healthy. This matches the design but should be documented, since the diagnosis depends on it.

K6 (Medium). The collector manager's docstring contradicts its code. verify_runtime_binding() says verification never samples a fault outcome, but execute_investigation_test() uses the verification inspect as evidence. The behaviour is deliberate, so the docstring should change.

K7 (Low). Resources are not closed. NetdataCollector's requests.Session is never closed, and CollectorManager.close() is never called, so the owned Docker client is not released.

K8 (Low). Source hygiene. docker_logs.py and docker_events.py use CRLF line endings and mix French docstrings with English comments. docker_logs.collect() loads all of stdout and stderr into memory, limited only by tail.

Also noted: the restart-history path calls collect_on_demand() without checking that an orchestrator exists, but it is unused by the live path. execute_investigation_test() sets a timeout on the shared Docker client, which is safe only while investigations run one at a time, a condition the code assumes but does not enforce.

## 7. Lab services: worker, vote app, result app, images

L1 (High). Votes are lost during faults. The worker pops a vote from Redis with ListLeftPopAsync before writing it to Postgres. If the write fails, the exception exits Main and the popped vote is gone. In the "Reconnecting DB" branch the popped vote is discarded without being written. The baseline check and the per-request HTTP 200 cannot detect this, so run records can count votes that never reached the database.

L2 (High). The result service does not recover after a database fault. server.js opens one pooled client, never releases it, and attaches no error handler. getVotes() reuses that client forever. When the database restarts, the connection is dead and never replaced, so the result page stays stale after the fault is reverted. The baseline check reads the votes table directly and so reports steady state while the result page is broken. This affects the postgres fault and any fault that restarts the database.

L3 (Medium). A single long vote crashes the worker. The vote app accepts any vote string and any voter_id cookie. The worker writes them into a VARCHAR(255) column, so an oversized value fails the insert and the retry, and the worker exits. Restart policy restores it, but the vote is lost and the worker is down meanwhile. One request can therefore look like an injected worker fault.

L4 (Medium). The voter ID drops a digit. hex(random.getrandbits(64))[2:-1] strips the final character, leaving less entropy. IDs can collide, and because the worker upserts on id, one voter's vote can overwrite another's. This is inherited from the upstream app. Fix it in the vendored copy and note the local change in lab/README.md, since target.lock pins the upstream commit.

L5 (Medium). Stale code and hardcoded credentials in the result app. server.js calls path.resolve() without requiring path, but that route is never reached because express.static() serves index.html for "/" first. The database connection string is hardcoded.

L6 (Low). Images are unpinned and partly end-of-life. The vote and result Dockerfiles use python:3.11-slim and node:18-slim tags, and lab/seed-data uses python:3.9-slim, which is end of life. Pin digests, as redis and postgres already are.

L7 (Low). Containers run as root. None of the Dockerfiles sets USER.

L8 (Low). The result image installs nodemon globally, a development tool, and ships it in production.

## 8. Security

S1 (High). The Netdata container can control Docker. docker-compose.infrasage.yml mounts /var/run/docker.sock:ro. The read-only flag does not restrict the Docker API, which accepts create and exec requests over the socket. Anyone who can reach the Netdata container can run a privileged container and gain host control. The container also runs with SYS_PTRACE and apparmor:unconfined, which contradicts the file's own comments about isolation.

S2 (Medium). Lab services have default credentials on host ports. Postgres is on 127.0.0.1:5432 with postgres/postgres, and Redis on 127.0.0.1:6379 with no authentication. Any local process, including other users on a shared machine, can read or change the votes table.

S3 (Medium). Credentials are hardcoded in several places: the worker, the result app, the MCP server in opencode.json, and .env.example. Move them to environment variables set by compose, and generate them per install.

S4 (Medium). A committed file leaks a local path. The file named =0.5 contains pip output with /home/divinesword/INDIA/Infrasage/.venv/bin/python3, which contradicts the commit that removed machine-specific references. Delete it and check history for other leaks.

S5 (Low). netdata/netdata:stable is unpinned while the other images are pinned by digest.

## 9. CI, tooling, and hygiene

T1 (Medium). test_twin.sh is stale. It expects the decision "stub_cause", but investigate() is now the default, so the script fails at its own assertion. It also hardcodes "26 tests passed"; the suite has 32.

T2 (Medium). CI does not use the Docker service it starts. The docker:24-dind service is never pointed to with DOCKER_HOST. The workflow waits with a fixed sleep 60 on top of --wait, and teardown has no if: always(), so a failed run leaves the lab running on the runner.

T3 (Medium). Dependencies are not pinned. requirements.txt uses >= ranges everywhere, so results may not reproduce, and a new pyJoules or docker release could change energy or Docker behaviour.

T4 (Low). Empty and orphan files. probability_estimator.py is empty and unused. compare_policies.py, api/routes.py, backend/main.py, and frontend/src/App.tsx are empty, so there is no policy comparison, no HTTP API, and no frontend.

T5 (Low). run_live_investigation() has its docstring duplicated, which looks like a merge artifact.

T6 (Low). The live orchestrator caps investigations at 30 seconds, while workloads.yaml allows 120 for the same step.

T7 (Low). STATUS.md is not verifiable. It cites 17 validated run records in a gitignored directory absent from the branch, and gives inconsistent test counts (26 in Phase 3, 32 in Phase 7).

## 10. Coverage

Reviewed: the twin (runner, injector, lab, workload, energy client, validation, repair, scoring, ground-truth store), the diagnosis core (Bayesian engine, models, planner, orchestrator, adapter, normaliser), the collectors (Docker, Docker logs and events, Netdata, TCP probe and helper, base), the collector manager, the lab (compose override, worker, vote and result apps, Dockerfiles, healthchecks, seed scripts), the CI workflow, and the run-record schema.

Not reviewed line by line: most of netdata.py's derivation logic, the full catalogue_loader.py, the planner's remaining code, the probability values in probabilities.json, the contents of the test files, and the JSON schema beyond the properties listed above. The frontend is empty. A full pass over these may turn up more issues.
