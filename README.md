# InfraSage

InfraSage investigates infrastructure problems by choosing diagnostic checks,
collecting real evidence and updating the probabilities of possible causes.
The current version is a research prototype tested with Docker’s Example Voting App.

## Quick start

You need **Python 3.12 or newer**, Python venv/pip support, **Docker Engine** and
**Docker Compose v2**. Docker must be running and accessible to your user.
Run these commands in the repository directory:

```bash
# First time: install dependencies and build the diagnostic helper.
./infrasage.sh setup

# Start the Voting App, verify it works and run one investigation.
./infrasage.sh start
```

Setup needs internet access to install dependencies and download the helper base
image. The first start may also download the five pinned application images.
Download/build time depends on your connection; later starts reuse local images.
No virtual environment activation or manual container IDs are needed.

`start` creates an isolated lab on the first run and reuses its ownership-checked
project on later runs. It prints the Vote and Result URLs; open those addresses in
your browser. Ports are assigned dynamically. It also submits a synthetic vote and
checks that it reaches PostgreSQL before launching an investigation.

After startup:

1. Open the printed **Vote** URL and choose an option.
2. Open the printed **Result** URL to see the votes.
3. Read the investigation JSON in the terminal for the checks, evidence and result.
4. Run `./infrasage.sh investigate` whenever you want another diagnostic run.

Running `./infrasage.sh` with no arguments is the same as `./infrasage.sh start`.
You can also call the script by its full path from another directory.

## Everyday commands

```bash
./infrasage.sh status       # Container states and current application URLs
./infrasage.sh investigate  # Run another investigation against the current app
./infrasage.sh help         # Command reference
```

Use `investigate` to inspect an existing problem: it does not restart the application
or require a healthy voting workflow. Use `start` when bringing up the lab; its
baseline verification must succeed before the investigation starts.
The application stays running after an investigation finishes.

## How it works

```text
Start the Voting App
    → Resolve and validate its Redis and worker targets
    → Choose a diagnostic using information gain and estimated energy cost
    → Collect evidence and update possible-cause probabilities
    → Repeat within the test/time/energy limits
    → Return a diagnosis or ABSTAIN with a detailed JSON trace
```

An investigation currently has three connected live checks:

- Is the Redis container running?
- Is the worker container running?
- Can a disposable helper in the worker’s network namespace connect to Redis?

If Redis has no current IP address (for example, after it stops), investigation
still checks its container state and skips the TCP check.

The planner chooses the checks automatically once an investigation starts.
The launcher runs one investigation and then exits. **Continuous monitoring,
automatic incident detection and automatic repairs are not implemented.**
The web dashboard is not started by this launcher; the investigation output is
printed in the terminal.
The HTTP server and InfraSage dashboard are currently empty placeholders; you do not need
Node.js or a separate web server to run this pipeline. The Vote and Result pages
are provided by the Voting App containers.

## Reading the result

The JSON includes observations, updated beliefs, selected checks, estimated costs
and the final stopping reason. **ABSTAIN means there is insufficient evidence to
name a cause confidently.** A successful command exit means the investigation
finished; check the trace for failed or unavailable probes.

Priors, likelihoods and energy costs are provisional research values. They are not
calibrated confidence scores or measured energy consumption. All four recorded
real Voting App validation scenarios abstained, and some fault probes failed or
timed out. This is a working investigation pipeline, not validated production
root-cause diagnosis.

## Troubleshooting

- **Docker unavailable:** start Docker and check your user’s Docker access.
- **Missing dependencies or outdated helper:** rerun `./infrasage.sh setup`.
- **Startup verification fails:** use `./infrasage.sh status` and inspect the lab
  containers’ logs. A healthy check allows about 75 seconds for the vote path.
- **Manifest/ownership error:** inspect `lab/voting-app-manifest.json` and the
  existing Docker resources. The launcher refuses to silently adopt unrelated
  containers. Do not delete the manifest to bypass a setup failure.
- **Existing application is broken:** use `investigate`; `start` requires a healthy
  baseline. Controlled fault testing and explicit restoration are documented below.

The generated manifest contains machine-specific resource identities and is not
committed. Preserve it while its lab exists. This launcher does not remove lab
containers, networks or database volumes.

## Development and detailed documentation

Run the mathematical example without Docker:

```bash
.venv/bin/python -m backend.diagnosis.example
```

Run the combined diagnostic and twin automated tests:

```bash
.venv/bin/python -m pytest -q tests backend/twin/tests
```

- [Mathematical engine, assumptions and simulation commands](docs/MATHEMATICAL_ENGINE.md)
- [Live investigation configuration and trace format](docs/PHASE2B2_AUTONOMOUS_INVESTIGATION.md)
- [Voting App validation, controlled faults and restoration](docs/PHASE2C_VOTING_APP_VALIDATION.md)
- [Worker-to-Redis diagnostic and helper details](docs/PHASE2B1_WORKER_REDIS_CONNECTIVITY.md)
- [Pipeline fault-test results and readiness improvements](docs/PIPELINE_READINESS.md)

## Integrated prototype

The public Python entry point `backend.api.investigate.investigate(LabTarget)` now
connects the twin to the same live orchestrator, collectors, adapter and Bayesian
engine used by the CLI. It resolves project containers and a locally verified,
immutable helper image instead of relying on a machine-specific image ID. Missing
helper/endpoint prerequisites disable TCP probing while retaining runtime checks.

The demo lab uses `lab/compose.yaml`, pinned prebuilt images and an ownership
manifest. The disposable twin uses `lab/compose.twin.yaml` plus
`lab/docker-compose.infrasage.yml`, vendored service sources and the fixed
`infrasage-lab` project. Use the instructions in [lab/README.md](lab/README.md)
for the twin. The two labs have separate lifecycles; never apply the twin override
to the demo Compose file. Optional Netdata starts only with `--profile monitoring`.

The twin retains its experiment runner, repair catalogue and validator, ground
truth store, split assignment, schema, aggregation and optional energy path.
Hardware energy readings describe host CPU package energy over a diagnostic
interval. CPU-based fallback values are rough estimates, labeled `estimated`;
neither establishes attributable per-test energy savings. Energy telemetry errors
do not discard valid diagnostic evidence.

The four earlier saved readiness scenarios used two probes. They remain historical
evidence, not validation of the integrated three-probe version. The combined tests
exercise software contracts with fake Docker and local HTTP fixtures. Fresh live
experiments are required before reporting diagnosis accuracy, calibrated models,
validated repairs or energy savings for this merged version. Some twin
self-validation checks explicitly remain unverified.

See [the merge report](docs/PROTOTYPE_INTEGRATION.md) for scope and validation.
