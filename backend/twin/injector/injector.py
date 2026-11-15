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
    """Resolve by Compose labels (guide §5.2). Returns full 64-char container ID."""
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
    short_id = ids[0]
    # Get full 64-char ID via inspect
    proc = docker.run("inspect", "-f", "{{.Id}}", short_id)
    if proc.returncode or not proc.stdout.strip():
        raise RuntimeError(f"could not get full ID for {short_id}")
    return proc.stdout.strip()


def _running(cid: str) -> bool:
    proc = docker.run("inspect", "-f", "{{.State.Running}}", cid)
    return proc.returncode == 0 and proc.stdout.strip() == "true"


def _apply_network_partition(fault: dict, project: str) -> tuple[str, str]:
    """Disconnect worker from back-tier network to partition from redis."""
    worker_cid = container("worker", project)
    injected_at = _now()
    # Disconnect worker from back-tier network
    _check(docker.run("network", "disconnect", "infrasage-lab_back-tier", worker_cid, timeout=_timeout()),
           f"network disconnect worker from back-tier")
    # Verify: worker should not be able to reach redis
    # Check network config - worker should not have back-tier network
    proc = docker.run("inspect", "-f", "{{json .NetworkSettings.Networks}}", worker_cid)
    if proc.returncode == 0:
        import json
        try:
            networks = json.loads(proc.stdout.strip())
            if "infrasage-lab_back-tier" not in networks:
                return injected_at, worker_cid
        except Exception:
            pass
    raise RuntimeError("Network partition verification failed: worker still connected to back-tier")


def _revert_network_partition(fault: dict, project: str) -> tuple[str, str]:
    """Reconnect worker to back-tier network."""
    worker_cid = container("worker", project)
    reverted_at = _now()
    # Reconnect worker to back-tier network
    _check(docker.run("network", "connect", "infrasage-lab_back-tier", worker_cid, timeout=_timeout()),
           f"network connect worker to back-tier")
    # Verify: worker should be connected to back-tier again
    proc = docker.run("inspect", "-f", "{{json .NetworkSettings.Networks}}", worker_cid)
    if proc.returncode == 0:
        import json
        try:
            networks = json.loads(proc.stdout.strip())
            if "infrasage-lab_back-tier" in networks:
                return reverted_at, worker_cid
        except Exception:
            pass
    raise RuntimeError("Network partition revert verification failed: worker not reconnected to back-tier")


def _apply_dns_override(fault: dict, project: str) -> tuple[str, str]:
    """Override /etc/hosts in worker to point redis to wrong IP."""
    worker_cid = container("worker", project)
    wrong_ip = fault["injection"]["parameters"]["wrong_ip"]
    injected_at = _now()
    # Backup /etc/hosts and add wrong entry
    _check(docker.run("exec", worker_cid, "cp", "/etc/hosts", "/etc/hosts.infrasage.bak", timeout=_timeout()),
           "backup /etc/hosts")
    _check(docker.run("exec", worker_cid, "sh", "-c", f"echo '{wrong_ip} redis' >> /etc/hosts", timeout=_timeout()),
           "add wrong redis entry to /etc/hosts")
    # Verify: redis should resolve to wrong IP
    proc = docker.run("exec", worker_cid, "getent", "hosts", "redis", timeout=_timeout())
    if proc.returncode == 0 and wrong_ip in proc.stdout:
        return injected_at, worker_cid
    raise RuntimeError(f"DNS override verification failed: redis does not resolve to {wrong_ip}")


def _revert_dns_override(fault: dict, project: str) -> tuple[str, str]:
    """Restore /etc/hosts in worker."""
    worker_cid = container("worker", project)
    reverted_at = _now()
    _check(docker.run("exec", worker_cid, "cp", "/etc/hosts.infrasage.bak", "/etc/hosts", timeout=_timeout()),
           "restore /etc/hosts")
    # Verify: redis should resolve to correct IP again
    proc = docker.run("exec", worker_cid, "getent", "hosts", "redis", timeout=_timeout())
    if proc.returncode == 0 and "172.18." in proc.stdout:  # Compose internal IP range
        return reverted_at, worker_cid
    raise RuntimeError("DNS override revert verification failed: redis not restored to correct IP")


def _apply_chaos(fault: dict, project: str) -> tuple[str, str]:
    """Send STOP signal to worker process (unmodeled fault)."""
    worker_cid = container("worker", project)
    injected_at = _now()
    _check(docker.run("kill", "--signal=STOP", worker_cid, timeout=_timeout()), "docker kill --signal=STOP worker")
    # Verify: worker process should be stopped (not running)
    # Check if container is still running but process is stopped
    # We can check by trying to exec a command - it should hang or fail
    import time
    time.sleep(0.5)  # Give signal time to take effect
    proc = docker.run("exec", worker_cid, "true", timeout=1)
    # If exec fails or times out, process is stopped
    if proc.returncode != 0:
        return injected_at, worker_cid
    raise RuntimeError("Chaos injection verification failed: worker process still responding")


def _revert_chaos(fault: dict, project: str) -> tuple[str, str]:
    """Send CONT signal to resume worker."""
    worker_cid = container("worker", project)
    reverted_at = _now()
    _check(docker.run("kill", "--signal=CONT", worker_cid, timeout=_timeout()), "docker kill --signal=CONT worker")
    # Verify: worker should respond again
    import time
    time.sleep(0.5)
    proc = docker.run("exec", worker_cid, "true", timeout=2)
    if proc.returncode == 0:
        return reverted_at, worker_cid
    raise RuntimeError("Chaos revert verification failed: worker process not responding")


def apply(name: str, project: str = LAB_PROJECT) -> dict:
    """Inject, then verify. ``status`` is ``verified`` only if the container state changed."""
    fault = _fault(name)
    method = fault["injection"]["method"]

    if method == "docker_stop":
        cid = container(fault["target_service"], project)
        injected_at = _now()
        _check(docker.run("stop", cid, timeout=_timeout()), f"docker stop {cid}")
        return {
            "method": method,
            "status": "verified" if not _running(cid) else "failed",
            "injected_at": injected_at,
            "target_service": fault["target_service"],
        }

    elif method == "network_partition":
        injected_at, target_cid = _apply_network_partition(fault, project)
        return {
            "method": method,
            "status": "verified",
            "injected_at": injected_at,
            "target_service": fault["target_service"],
        }

    elif method == "dns_override":
        injected_at, target_cid = _apply_dns_override(fault, project)
        return {
            "method": method,
            "status": "verified",
            "injected_at": injected_at,
            "target_service": fault["target_service"],
        }

    elif method == "chaos":
        injected_at, target_cid = _apply_chaos(fault, project)
        return {
            "method": method,
            "status": "verified",
            "injected_at": injected_at,
            "target_service": fault["target_service"],
        }

    else:
        raise ValueError(f"{name}: injection method {method!r} is not implemented yet")


def revert(name: str, project: str = LAB_PROJECT) -> dict:
    """Undo an injection and verify the container is running again."""
    fault = _fault(name)
    method = fault["revert"]["method"]

    if method == "docker_start":
        cid = container(fault["target_service"], project)
        reverted_at = _now()
        _check(docker.run("start", cid, timeout=_timeout()), f"docker start {cid}")
        return {
            "method": method,
            "status": "verified" if _running(cid) else "failed",
            "reverted_at": reverted_at,
            "target_service": fault["target_service"],
        }

    elif method == "network_partition_revert":
        reverted_at, target_cid = _revert_network_partition(fault, project)
        return {
            "method": method,
            "status": "verified",
            "reverted_at": reverted_at,
            "target_service": fault["target_service"],
        }

    elif method == "dns_override_revert":
        reverted_at, target_cid = _revert_dns_override(fault, project)
        return {
            "method": method,
            "status": "verified",
            "reverted_at": reverted_at,
            "target_service": fault["target_service"],
        }

    elif method == "chaos_revert":
        reverted_at, target_cid = _revert_chaos(fault, project)
        return {
            "method": method,
            "status": "verified",
            "reverted_at": reverted_at,
            "target_service": fault["target_service"],
        }

    else:
        raise ValueError(f"{name}: revert method {method!r} is not implemented yet")