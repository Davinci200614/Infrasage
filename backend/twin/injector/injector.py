"""Fault injection with its own verification (guide §7.4). Faults come from the catalogue."""

from __future__ import annotations

from datetime import datetime, timezone

from backend.twin import LAB_PROJECT, catalogues, docker


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _timeout() -> float:
    return catalogues.load("workloads")["timeouts_seconds"]["injection_verify"]


def _fault(name: str) -> dict:
    faults = catalogues.load("faults")["faults"]
    if name not in faults:
        raise KeyError(f"{name!r} is not in the fault catalogue")
    return faults[name]


def _check(proc, action: str):
    if proc.returncode:
        raise RuntimeError(f"{action} failed ({proc.returncode}): {(proc.stderr or proc.stdout).strip()}")
    return proc


def container(service: str, project: str = LAB_PROJECT) -> str:
    """Resolve by Compose labels, never by generated name (guide §5.2)."""
    proc = docker.run(
        "ps",
        "-aq",
        "--filter",
        f"label=com.docker.compose.project={project}",
        "--filter",
        f"label=com.docker.compose.service={service}",
    )
    ids = proc.stdout.split()
    if proc.returncode or len(ids) != 1:
        raise RuntimeError(f"expected one {project}/{service} container, found {ids or 'none'}")
    return ids[0]


def _running(cid: str) -> bool:
    proc = docker.run("inspect", "-f", "{{.State.Running}}", cid)
    return proc.returncode == 0 and proc.stdout.strip() == "true"


def apply(name: str, project: str = LAB_PROJECT) -> dict:
    """Inject, then verify. ``status`` is ``verified`` only if the container state changed."""
    fault = _fault(name)
    method = fault["injection"]["method"]
    if method != "docker_stop":
        raise ValueError(f"{name}: injection method {method!r} is not implemented yet")
    cid = container(fault["target_service"], project)
    injected_at = _now()
    _check(docker.run("stop", cid, timeout=_timeout()), f"docker stop {cid}")
    return {
        "method": method,
        "status": "verified" if not _running(cid) else "failed",
        "injected_at": injected_at,
        "target_service": fault["target_service"],
    }


def revert(name: str, project: str = LAB_PROJECT) -> dict:
    """Undo an injection and verify the container is running again."""
    fault = _fault(name)
    method = fault["revert"]["method"]
    if method != "docker_start":
        raise ValueError(f"{name}: revert method {method!r} is not implemented yet")
    cid = container(fault["target_service"], project)
    reverted_at = _now()
    _check(docker.run("start", cid, timeout=_timeout()), f"docker start {cid}")
    return {
        "method": method,
        "status": "verified" if _running(cid) else "failed",
        "reverted_at": reverted_at,
        "target_service": fault["target_service"],
    }
