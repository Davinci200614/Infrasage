"""Aggregation of run records into per-fault calibration/evaluation summaries."""

from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path

RUNS_DIR = Path("experiments/runs")
AGGREGATES_DIR = Path("experiments/aggregates")


def load_runs(split: str | None = None) -> list[dict]:
    """Load all run records, optionally filtered by split."""
    runs = []
    for path in RUNS_DIR.glob("*.json"):
        with open(path) as f:
            record = json.load(f)
        if "validation" in record.get("run_id", ""):
            continue
        if split is None or record.get("split") == split:
            runs.append(record)
    return runs


def aggregate_by_fault(runs: list[dict]) -> dict:
    """Aggregate run records by fault scenario."""
    by_fault = defaultdict(lambda: {
        "total": 0,
        "valid": 0,
        "by_lab_status": defaultdict(int),
        "by_decision": defaultdict(int),
        "by_test_outcome": defaultdict(lambda: defaultdict(int)),
        "correct": 0,
        "abstained": 0,
        "test_counts": defaultdict(int),
        "outcome_counts": defaultdict(lambda: defaultdict(int)),
    })

    for run in runs:
        fault = run.get("scenario")
        if not fault:
            continue
        agg = by_fault[fault]
        agg["total"] += 1
        lab_status = run.get("lab_status")
        agg["by_lab_status"][lab_status] += 1
        if lab_status == "valid":
            agg["valid"] += 1
            diag = run.get("diagnosis", {})
            decision = diag.get("decision", "unknown")
            agg["by_decision"][decision] += 1
            scored = run.get("scored", {})
            if scored.get("correct"):
                agg["correct"] += 1
            if scored.get("abstained"):
                agg["abstained"] += 1
            for test in diag.get("tests", []):
                test_id = test.get("test_id", "unknown")
                outcome = test.get("outcome", "unknown")
                exec_status = test.get("execution_status", "unknown")
                agg["test_counts"][test_id] += 1
                agg["outcome_counts"][test_id][outcome] += 1
                agg["by_test_outcome"][test_id][exec_status] += 1

    return {k: dict(v) for k, v in by_fault.items()}


def compute_likelihood_tables(runs: list[dict]) -> dict:
    """Compute P(outcome | cause, test) from calibration runs (guide §14)."""
    from backend.diagnosis.catalogue_loader import load_catalogues
    catalogue = load_catalogues(research_mode=True)
    counts = defaultdict(lambda: defaultdict(lambda: defaultdict(int)))
    for run in runs:
        if (run.get("split") != "calibration" or run.get("lab_status") != "valid"
                or run.get("injection", {}).get("status") != "verified"):
            continue
        label = run.get("ground_truth_label")
        if not label or label == "other_unknown":
            continue
        for test in run.get("diagnosis", {}).get("tests", []):
            tid, outcome = test.get("test_id"), test.get("outcome")
            if (test.get("execution_status") != "completed" or tid not in catalogue.tests
                    or outcome not in catalogue.tests[tid].outcomes):
                continue
            counts[label][tid][outcome] += 1
    alpha = 1.0
    likelihoods = {}
    for cause, tests in counts.items():
        likelihoods[cause] = {}
        for tid, outcomes in tests.items():
            vocabulary = catalogue.tests[tid].outcomes
            total = sum(outcomes.values())
            likelihoods[cause][tid] = {
                outcome: (outcomes[outcome] + alpha) / (total + len(vocabulary) * alpha)
                for outcome in vocabulary}

    return likelihoods


def main():
    AGGREGATES_DIR.mkdir(parents=True, exist_ok=True)

    all_runs = load_runs()
    print(f"Loaded {len(all_runs)} total runs")

    cal_runs = [r for r in all_runs if r.get("split") == "calibration"]
    eval_runs = [r for r in all_runs if r.get("split") == "evaluation"]
    print(f"Calibration runs: {len(cal_runs)}, Evaluation runs: {len(eval_runs)}")

    # Aggregate by fault
    cal_agg = aggregate_by_fault(cal_runs)
    eval_agg = aggregate_by_fault(eval_runs)

    # Compute likelihoods from calibration
    likelihoods = compute_likelihood_tables(cal_runs)

    # Save aggregates
    with open(AGGREGATES_DIR / "calibration.json", "w") as f:
        json.dump(cal_agg, f, indent=2)
    with open(AGGREGATES_DIR / "evaluation.json", "w") as f:
        json.dump(eval_agg, f, indent=2)
    with open(AGGREGATES_DIR / "likelihoods.json", "w") as f:
        json.dump(likelihoods, f, indent=2)

    print(f"Saved aggregates to {AGGREGATES_DIR}")
    print("\nCalibration summary:")
    for fault, agg in cal_agg.items():
        print(f"  {fault}: total={agg['total']}, valid={agg['valid']}, correct={agg['correct']}, abstained={agg['abstained']}")
        for test_id, count in agg["test_counts"].items():
            outcomes = agg["outcome_counts"][test_id]
            print(f"    {test_id}: {count} runs, outcomes={dict(outcomes)}")

    print("\nEvaluation summary:")
    for fault, agg in eval_agg.items():
        print(f"  {fault}: total={agg['total']}, valid={agg['valid']}, correct={agg['correct']}, abstained={agg['abstained']}")


if __name__ == "__main__":
    main()