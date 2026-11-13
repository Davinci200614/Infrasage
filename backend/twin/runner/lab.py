"""Lab lifecycle under the fixed ``infrasage-lab`` project (guide §5, §8.4, §18 step 1)."""

from __future__ import annotations

import json
import time
from uuid import uuid4

from backend.twin import LAB, LAB_PROJECT, catalogues, docker
from backend.twin.workload.generator import submit

SERVICES = ("vote", "result", "redis", "db", "worker")
PROJECT_LABEL = f"label=com.docker.compose.project={LAB_PROJECT}"
COMPOSE = (
    "compose",
    "-p",
    LAB_PROJECT,
    "-f",
    str(LAB / "compose.yaml"),
    "-f",
    str(LAB / "docker-compose.infrasage.yml"),
)

TIMEOUTS = catalogues.load("workloads")["timeouts_seconds"]
WORKLOAD = catalogues.load("workloads")["workload"]


def _check(proc, action: str):
    if proc.returncode:
        raise RuntimeError(f"{action} failed ({proc.returncode}): {(proc.stderr or proc.stdout).strip()}")
    return proc


def up() -> None:
    _check(docker.run(*COMPOSE, "up", "-d", "--wait", timeout=TIMEOUTS["stack_up"]), "compose up")


def down() -> None:
    _check(docker.run(*COMPOSE, "down", "-v", "--remove-orphans", timeout=TIMEOUTS["teardown"]), "compose down")


def reset() -> None:
    down()
    up()


def _rows(text: str) -> list[dict]:
    """``compose ps --format json`` emits a JSON array on new Compose, NDJSON on old."""
    text = text.strip()
    if not text:
        return []
    if text.startswith("["):
        return json.loads(text)
    return [json.loads(line) for line in text.splitlines() if line.strip()]


def service_states() -> dict[str, str]:
    """Service name -> Compose state ("running", "exited", ...)."""
    proc = _check(docker.run(*COMPOSE, "ps", "--all", "--format", "json", timeout=30), "compose ps")
    return {
        (row.get("Service") or row.get("service") or ""): (row.get("State") or row.get("state") or "")
        for row in _rows(proc.stdout)
    }


def residue() -> dict[str, list[str]]:
    """What the project still owns. All lists empty means a clean teardown (guide §8.4)."""
    queries = {
        "containers": ("ps", "-aq", "--filter", PROJECT_LABEL),
        "volumes": ("volume", "ls", "-q", "--filter", PROJECT_LABEL),
        "networks": ("network", "ls", "-q", "--filter", PROJECT_LABEL),
    }
    found: dict[str, list[str]] = {}
    for kind, args in queries.items():
        found[kind] = _check(docker.run(*args, timeout=30), f"list {kind}").stdout.split()
    return found


def check_baseline(target: str | None = None, timeout: float | None = None) -> dict:
    """Steady state before injection (guide §5.3). Returns ``{"ok", "reason", ...}``."""
    states = service_states()
    missing = [name for name in SERVICES if states.get(name) != "running"]
    if missing:
        return {"ok": False, "reason": f"services not running: {', '.join(missing)}"}

    ping = docker.run(*COMPOSE, "exec", "-T", "redis", "redis-cli", "ping", timeout=TIMEOUTS["injection_verify"])
    if ping.returncode or "PONG" not in ping.stdout:
        return {"ok": False, "reason": "redis did not answer PING"}

    pg = docker.run(*COMPOSE, "exec", "-T", "db", "pg_isready", "-U", "postgres", timeout=TIMEOUTS["injection_verify"])
    if pg.returncode:
        return {"ok": False, "reason": "postgres is not accepting connections"}

    # The result UI renders counts over socket.io, so the `votes` row is the proof
    # that the test vote crossed vote -> redis -> worker -> db (what `result` reads).
    vote = "a"
    voter = f"baseline-{uuid4().hex}"
    target = target or WORKLOAD["target"]
    try:
        submit(vote, voter, target)
    except OSError as exc:
        return {"ok": False, "reason": f"vote submission failed: {exc}"}

    deadline = time.monotonic() + (timeout or TIMEOUTS["baseline_vote"])
    sql = f"select vote from votes where id = '{voter}'"
    while time.monotonic() < deadline:
        row = docker.run(
            *COMPOSE, "exec", "-T", "db", "psql", "-U", "postgres", "-d", "postgres", "-tAc", sql,
            timeout=TIMEOUTS["injection_verify"],
        )
        if row.returncode == 0 and row.stdout.strip() == vote:
            return {"ok": True, "reason": None, "voter_id": voter}
        time.sleep(0.5)
    return {"ok": False, "reason": f"vote {voter} never reached the database"}
