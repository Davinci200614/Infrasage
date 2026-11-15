"""Twin self-validation (guide §15, §18 step 13).

Runs the seven validation checks and stores results in experiments/validation/.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from backend.twin import LAB, ROOT, catalogues


VALIDATION_DIR = ROOT / "experiments" / "validation"
FAULTS = catalogues.load("faults")["faults"]
WORKLOAD = catalogues.load("workloads")["workload"]
TIMEOUTS = catalogues.load("workloads")["timeouts_seconds"]


@dataclass
class ValidationResult:
    check: str
    passed: bool
    details: dict[str, Any]
    timestamp: str = None

    def __post_init__(self):
        if self.timestamp is None:
            self.timestamp = datetime.now(timezone.utc).isoformat()


def _persist(result: ValidationResult) -> None:
    VALIDATION_DIR.mkdir(parents=True, exist_ok=True)
    path = VALIDATION_DIR / f"{result.check}.json"
    path.write_text(json.dumps(asdict(result), indent=2) + "\n")


def run_all_validations() -> list[ValidationResult]:
    """Run all §15 validation checks."""
    results = []
    
    print("Running twin self-validation (§15)...")
    
    # 1. Reproducibility
    print("  1/7 Reproducibility...")
    r = check_reproducibility()
    results.append(r)
    _persist(r)
    
    # 2. Reset correctness
    print("  2/7 Reset correctness...")
    r = check_reset_correctness()
    results.append(r)
    _persist(r)
    
    # 3. Injection verification
    print("  3/7 Injection verification...")
    r = check_injection_verification()
    results.append(r)
    _persist(r)
    
    # 4. Fault-symptom coupling
    print("  4/7 Fault-symptom coupling...")
    r = check_fault_symptom_coupling()
    results.append(r)
    _persist(r)
    
    # 5. Isolation
    print("  5/7 Isolation...")
    r = check_isolation()
    results.append(r)
    _persist(r)
    
    # 6. Label separation (import guard)
    print("  6/7 Label separation...")
    r = check_label_separation()
    results.append(r)
    _persist(r)
    
    # 7. Determinism of seeds
    print("  7/7 Determinism of seeds...")
    r = check_seed_determinism()
    results.append(r)
    _persist(r)
    
    # Summary
    passed = sum(1 for r in results if r.passed)
    print(f"\nValidation complete: {passed}/{len(results)} checks passed")
    for r in results:
        status = "PASS" if r.passed else "FAIL"
        print(f"  {status}: {r.check}")
    
    return results


def check_reproducibility() -> ValidationResult:
    """Same seed + scenario, 5 runs -> identical injection params, comparable baselines."""
    from backend.twin.runner import experiment
    
    fault_name = "redis_process_stopped"
    seed = 42
    injection_params = []
    baselines = []
    
    for i in range(5):
        run_id = f"validation-repro-{fault_name}-{i:02d}"
        try:
            record = experiment.run(
                fault_name, seed=seed, run_id=run_id, 
                warmup_seconds=0.0, measure_energy=False
            )
            if record.get("lab_status") != "valid" or record.get("injection", {}).get("status") != "verified":
                return ValidationResult("reproducibility", False, {"run": i, "lab_status": record.get("lab_status")})
            injection_params.append(record["injection"])
            baselines.append(record.get("workload", {}).get("votes_submitted", 0))
        except Exception as e:
            return ValidationResult(
                check="reproducibility",
                passed=False,
                details={"error": str(e), "run": i},
            )
    
    # Check injection parameters are identical (except timestamp)
    first_params = {k: v for k, v in injection_params[0].items() if k != "injected_at"}
    identical = all(
        {k: v for k, v in p.items() if k != "injected_at"} == first_params
        for p in injection_params
    )
    
    # Baselines should be comparable (within reasonable range)
    baseline_range = max(baselines) - min(baselines) if baselines else 0
    comparable = baseline_range <= 5  # Allow small variance
    
    return ValidationResult(
        check="reproducibility",
        passed=identical and comparable,
        details={
            "identical_injection_params": identical,
            "baseline_votes_range": baseline_range,
            "baselines": baselines,
            "comparable_baselines": comparable,
        },
    )


def check_reset_correctness() -> ValidationResult:
    """After teardown, no lab residue. New baseline passes."""
    from backend.twin.runner import lab, experiment
    
    # Run a couple of experiments to generate residue
    for i in range(3):
        experiment.run(
            "redis_process_stopped", seed=420 + i, run_id=f"validation-reset-{i:02d}",
            warmup_seconds=0.0, measure_energy=False
        )
    
    # Check residue after last run
    residue = lab.residue()
    has_residue = any(residue.values())
    
    # Verify new baseline passes
    try:
        lab.up()
        baseline_ok = lab.check_baseline(timeout=TIMEOUTS["baseline_vote"])["ok"]
    finally:
        lab.down()
    
    return ValidationResult(
        check="reset_correctness",
        passed=not has_residue and baseline_ok,
        details={
            "residue": residue,
            "has_residue": has_residue,
            "baseline_passes": baseline_ok,
        },
    )


def check_injection_verification() -> ValidationResult:
    """Every fault's verification detects injected state in 100% of attempts."""
    from backend.twin.runner import lab
    from backend.twin.injector import injector
    
    results = {}
    
    for fault_name, fault in FAULTS.items():
        if not fault.get("applicable_to_v0_1", True):
            continue
        
        verified_count = 0
        attempts = 3
        
        for i in range(attempts):
            run_id = f"validation-inject-{fault_name}-{i:02d}"
            try:
                # Fresh lab for each attempt
                lab.reset()
                injection = injector.apply(fault_name)
                if injection.get("status") == "verified":
                    verified_count += 1
                injector.revert(fault_name)
                lab.down()
            except Exception:
                # Ensure cleanup on any error
                try:
                    lab.down()
                except Exception:
                    pass
        
        results[fault_name] = {
            "verified": verified_count,
            "attempts": attempts,
            "rate": verified_count / attempts if attempts > 0 else 0,
        }
    
    all_verified = all(r["rate"] == 1.0 for r in results.values())
    
    return ValidationResult(
        check="injection_verification",
        passed=all_verified,
        details={"per_fault": results, "all_100_percent": all_verified},
    )


def check_fault_symptom_coupling() -> ValidationResult:
    """Each modeled fault produces its expected symptom in the workload."""
    return ValidationResult("fault_symptom_coupling", False, {
        "status": "unverified",
        "reason": "A test executing does not verify the fault's effect on vote propagation."
    })


def check_isolation() -> ValidationResult:
    """Do not claim runtime network or log isolation from a Compose substring."""
    return ValidationResult("isolation", False, {
        "status": "unverified",
        "reason": "Cross-container reachability and diagnostic log leakage need runtime checks."
    })


def check_label_separation() -> ValidationResult:
    """Automated test confirms no diagnostic module imports backend.twin."""
    # This is already enforced by test_isolation.py
    # Run it programmatically using the current python executable
    import subprocess
    import sys
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "backend/twin/tests/test_isolation.py", "-q"],
        capture_output=True, text=True, cwd=ROOT,
    )
    passed = result.returncode == 0
    
    return ValidationResult(
        check="label_separation",
        passed=passed,
        details={
            "test_isolation_passed": passed,
            "stdout": result.stdout,
            "stderr": result.stderr,
        },
    )


def check_seed_determinism() -> ValidationResult:
    """Splits and workload sequences reproducible from seed."""
    from backend.twin.runner.splits import assign
    from backend.twin.workload.generator import run as run_workload
    
    # Test split assignment determinism
    seed = 12345
    splits = [assign(seed) for _ in range(10)]
    split_deterministic = all(s == splits[0] for s in splits)
    
    # Test workload determinism (same seed -> same voter_ids)
    workload_results = []
    for _ in range(3):
        votes = run_workload(
            seconds=1.0, target=str(WORKLOAD["target"]),
            rate=float(WORKLOAD["rate_per_second"]), seed=seed
        )
        voter_ids = [v["voter_id"] for v in votes]
        workload_results.append(voter_ids)
    
    workload_deterministic = all(v == workload_results[0] for v in workload_results)
    
    return ValidationResult(
        check="seed_determinism",
        passed=split_deterministic and workload_deterministic,
        details={
            "split_deterministic": split_deterministic,
            "splits": splits,
            "workload_deterministic": workload_deterministic,
            "sample_voter_ids": workload_results[0][:5] if workload_results else [],
        },
    )


if __name__ == "__main__":
    results = run_all_validations()
    # Exit with error if any validation failed
    if not all(r.passed for r in results):
        exit(1)