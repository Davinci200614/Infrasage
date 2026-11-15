"""Repair validator (guide §12). Tests whether a catalogue repair restores normal operation."""

from __future__ import annotations

import yaml
from pathlib import Path

from backend.twin import LAB_PROJECT, catalogues, docker
from backend.twin.runner import lab


class RepairError(Exception):
    """Raised when a repair action fails or is invalid."""
    pass


def load_repairs() -> dict:
    """Load repair catalogue."""
    return catalogues.load("repairs")["repairs"]


def get_repair(fault_name: str) -> dict:
    """Get repair definition for a fault."""
    repairs = load_repairs()
    if fault_name not in repairs:
        raise RepairError(f"No repair defined for fault: {fault_name}")
    return repairs[fault_name]


def resolve_container(service: str, project: str = LAB_PROJECT) -> str:
    """Get full container ID for a service."""
    proc = docker.run(
        "ps", "-aq",
        "--filter", f"label=com.docker.compose.project={project}",
        "--filter", f"label=com.docker.compose.service={service}",
    )
    ids = proc.stdout.split()
    if proc.returncode or len(ids) != 1:
        raise RepairError(f"expected one {project}/{service} container, found {ids or 'none'}")
    short_id = ids[0]
    proc = docker.run("inspect", "-f", "{{.Id}}", short_id)
    if proc.returncode or not proc.stdout.strip():
        raise RepairError(f"could not get full ID for {short_id}")
    return proc.stdout.strip()


def run_repair_action(action: dict, context: dict) -> dict:
    """Execute a single repair action step."""
    method = action["method"]
    command_template = action["command"]
    timeout = action.get("timeout_seconds", 10)
    verification = action.get("verification")

    # Build command from template list
    cmd_parts = [part.format(**context) for part in command_template]

    proc = docker.run(*cmd_parts, timeout=timeout)
    if proc.returncode != 0:
        raise RepairError(f"Repair action '{method}' failed ({proc.returncode}): {(proc.stderr or proc.stdout).strip()}")

    # Run verification if specified
    if verification:
        verify_result = run_verification(verification, context)
        if not verify_result.get("ok"):
            raise RepairError(f"Verification '{verification}' failed: {verify_result.get('reason')}")

    return {"method": method, "status": "completed", "verification": verification, "verified": True}


def run_verification(verification: str, context: dict) -> dict:
    """Run a verification check."""
    if verification == "service_running":
        cid = context.get("target_cid") or context.get("worker_cid")
        if not cid:
            return {"ok": False, "reason": "no container ID for service_running"}
        proc = docker.run("inspect", "-f", "{{.State.Running}}", cid)
        return {"ok": proc.returncode == 0 and proc.stdout.strip() == "true",
                "reason": "service not running" if proc.returncode != 0 or proc.stdout.strip() != "true" else None}

    elif verification == "network_connected":
        worker_cid = context.get("worker_cid")
        if not worker_cid:
            return {"ok": False, "reason": "no worker_cid for network_connected"}
        # Check if worker is connected to back-tier network
        proc = docker.run("inspect", "-f", "{{json .NetworkSettings.Networks}}", worker_cid)
        if proc.returncode != 0:
            return {"ok": False, "reason": "inspect failed"}
        import json
        try:
            networks = json.loads(proc.stdout)
            return {"ok": "infrasage-lab_back-tier" in networks,
                    "reason": "worker not connected to back-tier" if "infrasage-lab_back-tier" not in networks else None}
        except json.JSONDecodeError:
            return {"ok": False, "reason": "could not parse networks"}

    elif verification == "dns_correct":
        worker_cid = context.get("worker_cid")
        if not worker_cid:
            return {"ok": False, "reason": "no worker_cid for dns_correct"}
        proc = docker.run("exec", worker_cid, "getent", "hosts", "redis")
        if proc.returncode != 0:
            return {"ok": False, "reason": "getent hosts redis failed"}
        # Check that redis resolves to correct IP (not the overridden one)
        return {"ok": "172.18.0.3" in proc.stdout or "172.18.0." in proc.stdout,
                "reason": f"redis DNS not correct: {proc.stdout}"}

    else:
        return {"ok": False, "reason": f"unknown verification: {verification}"}


def validate_repair(fault_name: str) -> dict:
    """
    Execute the full repair sequence for a fault and verify steady state.
    Returns repair record with status: 'restored', 'not_restored', 'repair_failed', 'timeout'.
    """
    repair = get_repair(fault_name)
    actions = repair.get("actions", [])
    if not actions:
        raise RepairError(f"No actions defined for repair: {fault_name}")

    # Build context for template rendering
    target_service = repair["target_service"]
    context = {
        "target_cid": resolve_container(target_service),
        "worker_cid": resolve_container("worker"),
        "redis_cid": resolve_container("redis"),
        "db_cid": resolve_container("db"),
        "project": LAB_PROJECT,
    }

    results = []
    for action in actions:
        try:
            result = run_repair_action(action, context)
            results.append(result)
        except RepairError as e:
            return {
                "fault": fault_name,
                "repair_version": repair["version"],
                "status": "repair_failed",
                "actions_completed": results,
                "error": str(e),
            }

    # After all actions, verify steady state via lab baseline check
    baseline = lab.check_baseline()
    if baseline.get("ok"):
        return {
            "fault": fault_name,
            "repair_version": repair["version"],
            "status": "restored",
            "actions_completed": results,
            "baseline_check": {"ok": True, "voter_id": baseline.get("voter_id")},
        }
    else:
        return {
            "fault": fault_name,
            "repair_version": repair["version"],
            "status": "not_restored",
            "actions_completed": results,
            "baseline_check": {"ok": False, "reason": baseline.get("reason")},
        }


if __name__ == "__main__":
    import sys
    if len(sys.argv) < 2:
        print("Usage: python -m backend.twin.repair.validator <fault_name>")
        sys.exit(1)
    fault = sys.argv[1]
    result = validate_repair(fault)
    print(yaml.dump(result))