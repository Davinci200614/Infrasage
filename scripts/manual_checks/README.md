# Manual collector checks

These scripts print live Docker or Netdata observations. They require configured
services and do not form part of the automated pytest suite. They run only when
invoked explicitly; importing them does not execute a collector.

Run from the repository root, for example:

```bash
.venv/bin/python scripts/manual_checks/check_netdata_collector.py
.venv/bin/python scripts/manual_checks/check_docker_collector.py
.venv/bin/python scripts/manual_checks/check_full_collection.py
```

Some checks retain the historical `infrasage-test` container name. Adapt the
explicit target to your intended lab before using those scripts. The Netdata
error check intentionally uses port 19998 to exercise an unavailable endpoint.

The full disposable twin lifecycle script is `scripts/test_twin.sh`. It starts,
injects faults into and removes the dedicated twin stack, including its volumes.
It is separate from this directory's collector checks and from automated tests.
