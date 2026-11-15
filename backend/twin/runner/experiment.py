"""One full experiment run (guide §8.1 steps 1-11, §18 step 5).

Wires the lab lifecycle — reset, baseline, warm-up, verified injection, label
write, investigation, freeze, scoring, revert, teardown, persist — into one run
record. Steps that fail short-circuit with one of the guide's run statuses.
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import threading
import time
from collections.abc import Callable
from pathlib import Path

import yaml

from backend.api.investigate import investigate as real_investigate, InvestigationResult, LabTarget, TestResult
from backend.twin import LAB, ROOT, catalogues
from backend.twin.energy_client import EnergyClient, BaselineEnergy
from backend.twin.ground_truth import store
from backend.twin.injector import injector
from backend.twin.runner import lab
from backend.twin.runner.splits import assign
from backend.twin.workload.generator import run as run_workload

RUNS_DIR = ROOT / "experiments" / "runs"
SCHEMA_VERSION = "1.0"
ENVIRONMENT = "voting-app-v0.1"

TIMEOUTS = catalogues.load("workloads")["timeouts_seconds"]
WORKLOAD = catalogues.load("workloads")["workload"]

InvestigateFn = Callable[[LabTarget, EnergyClient | None], InvestigationResult]

_ENERGY_SERVER_PROC: subprocess.Popen | None = None
_ENERGY_SERVER_THREAD: threading.Thread | None = None
_PYTHON_EXE = os.environ.get("INFRASAGE_ENERGY_PYTHON", sys.executable)


def _start_energy_server() -> EnergyClient:
    """Start the energy measurement server as a background process."""
    global _ENERGY_SERVER_PROC, _ENERGY_SERVER_THREAD
    
    server_script = Path(__file__).resolve().parents[2] / "energy" / "meter.py"
    if not server_script.exists():
        # Fallback to the twin's energy_server.py if energy/meter.py not found
        server_script = Path(__file__).resolve().parents[1] / "energy_server.py"
    
    _ENERGY_SERVER_PROC = subprocess.Popen(
        [_PYTHON_EXE, str(server_script)],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    
    # Wait for server to be ready
    client = EnergyClient()
    for _ in range(20):
        time.sleep(0.25)
        health = client.health_check()
        if health.get("status") == "ok":
            return client
    
    # Server didn't start properly
    _stop_energy_server()
    raise RuntimeError("Energy server failed to start")


def _stop_energy_server() -> None:
    """Stop the energy measurement server. Idempotent."""
    global _ENERGY_SERVER_PROC, _ENERGY_SERVER_THREAD
    if _ENERGY_SERVER_PROC:
        proc = _ENERGY_SERVER_PROC
        _ENERGY_SERVER_PROC = None
        try:
            proc.terminate()
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()
        except Exception:
            pass  # Process already gone


class _RunError(Exception):
    """Internal carrier for a failed lifecycle step (guide §8.1 run statuses)."""

    def __init__(self, status: str) -> None:
        super().__init__(status)
        self.status = status


def stub_investigate(target: LabTarget, energy_client=None) -> InvestigationResult:
    """Fixed-answer stub for tests; not used in production runs."""
    return InvestigationResult(
        decision="stub_cause",
        abstained=False,
        posterior={"stub_cause": 1.0},
        tests=[],
        total_tests=0,
        elapsed_seconds=0.0,
    )


def run(
    fault_name: str,
    *,
    seed: int = int(WORKLOAD["seed"]),
    target: str | None = None,
    investigate: InvestigateFn = real_investigate,
    run_id: str | None = None,
    warmup_seconds: float | None = None,
    measure_energy: bool = False,
) -> dict:
    """Run one fault experiment end to end and return the persisted run record."""
    faults = catalogues.load("faults")["faults"]
    fault = faults[fault_name]
    rid = run_id or _next_run_id(fault_name)
    record = {
        "schema_version": SCHEMA_VERSION,
        "run_id": rid,
        "split": assign(seed),
        "seed": seed,
        "scenario": fault_name,
        "fault_version": int(fault["version"]),
        "environment": ENVIRONMENT,
        "target_commit": _target_commit(),
        "override_file_hash": _override_file_hash(),
        "lab_status": "valid",
        "injection": {"method": fault["injection"]["method"], "status": "pending", "injected_at": None},
        "workload": {
            "rate_per_second": float(WORKLOAD["rate_per_second"]),
            "warmup_seconds": float(WORKLOAD["warmup_seconds"]) if warmup_seconds is None else warmup_seconds,
            "votes_submitted": 0,
        },
        "diagnosis": {
            "decision": None,
            "abstained": False,
            "posterior": {},
            "tests": [],
            "total_tests": 0,
            "elapsed_seconds": 0.0,
        },
        "ground_truth_label": None,
        "scored": {"correct": False, "abstained": False},
        "baseline_energy": None,
    }
    injected: dict | None = None
    energy_client: EnergyClient | None = None
    try:
        # Start energy server and measure baseline BEFORE lab up (guide §13)
        if measure_energy:
            energy_client = _start_energy_server()
            lab.down()  # Baseline must exclude an existing twin stack.
            baseline = energy_client.measure_baseline(duration_seconds=10.0)
            record["baseline_energy"] = {
                "energy_joules": baseline.energy_joules,
                "energy_method": baseline.energy_method,
                "duration_seconds": baseline.duration_seconds,
                "hardware_context": baseline.hardware_context,
            }
        
        lab.reset()  # 2
        if not lab.check_baseline(target=target or str(WORKLOAD["target"]), timeout=TIMEOUTS["baseline_vote"])["ok"]:
            raise _RunError("invalid_baseline")  # 3
        votes = run_workload(
            seconds=float(WORKLOAD["warmup_seconds"]) if warmup_seconds is None else warmup_seconds,
            target=target or str(WORKLOAD["target"]),
            rate=float(WORKLOAD["rate_per_second"]),
            seed=seed,
        )
        record["workload"]["votes_submitted"] = sum(bool(v.get("ok")) for v in votes)
        injection = injector.apply(fault_name)  # 5
        record["injection"] = dict(injection)
        injected = injection
        if injection["status"] != "verified":
            raise _RunError("injection_failed")
        store.write(
            rid,
            label=str(fault["label_cause"]),
            fault_version=int(fault["version"]),
            injected_at=str(injection["injected_at"]),
            parameters=fault.get("injection", {}).get("parameters"),
        )  # 6
        # Pass energy client to investigation for per-test measurement
        result = investigate(LabTarget(), energy_client)  # 7
        record["diagnosis"] = _diagnosis(result)  # 8
        # The label reaches the record only now: diagnosis is frozen and scored.
        record["ground_truth_label"] = str(fault["label_cause"])
        record["scored"] = {"correct": result.decision == str(fault["label_cause"]), "abstained": result.abstained}  # 9
    except _RunError as err:
        record["lab_status"] = err.status
    except Exception:
        record["lab_status"] = "error"
    finally:
        if injected is not None and injected.get("status") == "verified":
            try:
                reverted = injector.revert(fault_name)
                if reverted.get("status") != "verified":
                    record["lab_status"] = "dirty"
            except Exception:
                record["lab_status"] = "dirty"
        try:
            lab.down()  # teardown
            if any(lab.residue().values()):
                record["lab_status"] = "dirty"
        except Exception:
            record["lab_status"] = "dirty"
        # Always attempt to stop energy server (idempotent)
        _stop_energy_server()
    _persist(record, rid)
    return record


def _diagnosis(result: InvestigationResult) -> dict:
    return {
        "decision": result.decision,
        "abstained": result.abstained,
        "posterior": dict(result.posterior or {}),
        "tests": [_test_result(test) for test in result.tests],
        "total_tests": result.total_tests,
        "elapsed_seconds": result.elapsed_seconds,
    }


def _test_result(test: TestResult) -> dict:
    return {
        "test_id": test.test_id,
        "outcome": test.outcome,
        "execution_status": test.execution_status,
        "observed_at": test.observed_at,
        "collector_version": test.collector_version,
        "latency_ms": test.latency_ms,
        "energy_joules": test.energy_joules,
        "energy_method": test.energy_method,
        "raw_reference": test.raw_reference,
        "target_identifier": test.target_identifier,
    }


def _target_commit() -> str:
    lock = yaml.safe_load((LAB / "target.lock").read_text()) or {}
    return str(lock.get("commit") or "")


def _override_file_hash() -> str:
    return hashlib.sha256((LAB / "docker-compose.infrasage.yml").read_bytes()).hexdigest()


def _next_run_id(fault_name: str) -> str:
    prefix = f"{fault_name}-"
    highest = 0
    if RUNS_DIR.is_dir():
        for path in RUNS_DIR.glob(f"{prefix}*.json"):
            suffix = path.stem[len(prefix) :]
            if suffix.isdigit():
                highest = max(highest, int(suffix))
    return f"{fault_name}-{highest + 1:04d}"


def _persist(record: dict, run_id: str) -> None:
    RUNS_DIR.mkdir(parents=True, exist_ok=True)
    (RUNS_DIR / f"{run_id}.json").write_text(json.dumps(record, indent=2) + "\n")
