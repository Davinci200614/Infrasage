
from datetime import datetime, timezone

import pytest

from backend.core.models import (
    BeliefState,
    Decision,
    DiagnosticTest,
    ExecutionStatus,
    Hypothesis,
    InvestigationResult,
    Observation,
    TestSelection as ModelTestSelection,
    validate_cost,
    validate_distribution,
)


def make_test():
    return DiagnosticTest(
        identifier="check_worker",
        outcomes=("running", "stopped"),
        likelihoods={
            "worker_ok": {"running": 0.9, "stopped": 0.1},
            "worker_failed": {"running": 0.2, "stopped": 0.8},
        },
        expected_energy_joules=1.0,
        expected_latency_seconds=0.5,
    )


def test_validate_distribution_accepts_valid_distribution():
    validate_distribution({"a": 0.4, "b": 0.6})


@pytest.mark.parametrize(
    "distribution",
    [
        {},
        {"": 1.0},
        {"a": -0.1, "b": 1.1},
        {"a": float("nan"), "b": 0.0},
        {"a": float("inf"), "b": 0.0},
        {"a": 0.2, "b": 0.2},
    ],
)
def test_validate_distribution_rejects_invalid_values(distribution):
    with pytest.raises(ValueError):
        validate_distribution(distribution)


def test_validate_distribution_accepts_small_floating_point_error():
    validate_distribution({"a": 0.5, "b": 0.5000000001})


def test_validate_cost_accepts_zero_and_positive_values():
    validate_cost(0, "cost")
    validate_cost(12.5, "cost")


@pytest.mark.parametrize(
    "value",
    [-1, float("nan"), float("inf"), float("-inf")],
)
def test_validate_cost_rejects_invalid_values(value):
    with pytest.raises(ValueError):
        validate_cost(value, "cost")


def test_hypothesis_creation():
    hypothesis = Hypothesis("worker_failed", "Worker is stopped")

    assert hypothesis.identifier == "worker_failed"
    assert hypothesis.description == "Worker is stopped"


def test_diagnostic_test_creation():
    test = make_test()

    assert test.identifier == "check_worker"
    assert test.outcomes == ("running", "stopped")


@pytest.mark.parametrize(
    "overrides",
    [
        {"identifier": ""},
        {"outcomes": ("running",)},
        {"outcomes": ("running", "running")},
        {"outcomes": ("running", "")},
        {"likelihoods": {}},
        {
            "likelihoods": {
                "worker_ok": {"running": 0.5, "stopped": 0.5},
                "worker_failed": {"running": 0.2, "stopped": 0.2},
            }
        },
        {"expected_energy_joules": -1},
        {"expected_latency_seconds": -1},
    ],
)
def test_diagnostic_test_rejects_invalid_data(overrides):
    values = {
        "identifier": "check_worker",
        "outcomes": ("running", "stopped"),
        "likelihoods": {
            "worker_ok": {"running": 0.9, "stopped": 0.1},
            "worker_failed": {"running": 0.2, "stopped": 0.8},
        },
        "expected_energy_joules": 1.0,
        "expected_latency_seconds": 0.5,
    }
    values.update(overrides)

    with pytest.raises(ValueError):
        DiagnosticTest(**values)


def test_execution_status_values():
    assert ExecutionStatus.COMPLETED.value == "completed"
    assert ExecutionStatus.FAILED.value == "failed"
    assert ExecutionStatus.UNAVAILABLE.value == "unavailable"


def test_completed_observation_requires_outcome():
    observation = Observation(
        test_identifier="check_worker",
        target_identifier="worker",
        execution_status=ExecutionStatus.COMPLETED,
        observed_outcome="running",
    )

    assert observation.observed_outcome == "running"


@pytest.mark.parametrize(
    "status,outcome",
    [
        (ExecutionStatus.COMPLETED, None),
        (ExecutionStatus.FAILED, "running"),
        (ExecutionStatus.UNAVAILABLE, "stopped"),
    ],
)
def test_observation_rejects_inconsistent_status_and_outcome(status, outcome):
    with pytest.raises(ValueError):
        Observation(
            test_identifier="check_worker",
            target_identifier="worker",
            execution_status=status,
            observed_outcome=outcome,
        )


@pytest.mark.parametrize(
    "kwargs",
    [
        {"duration_seconds": -1},
        {"duration_seconds": float("nan")},
        {"measured_energy_joules": -1},
        {"measured_energy_joules": float("inf")},
    ],
)
def test_observation_rejects_invalid_costs(kwargs):
    values = {
        "test_identifier": "check_worker",
        "target_identifier": "worker",
        "execution_status": ExecutionStatus.COMPLETED,
        "observed_outcome": "running",
    }
    values.update(kwargs)

    with pytest.raises(ValueError):
        Observation(**values)


def test_observation_default_timestamp_is_timezone_aware():
    observation = Observation(
        test_identifier="check_worker",
        target_identifier="worker",
        execution_status=ExecutionStatus.FAILED,
    )

    assert observation.timestamp.tzinfo is not None


def test_observation_accepts_explicit_timestamp():
    timestamp = datetime(2026, 1, 1, tzinfo=timezone.utc)

    observation = Observation(
        test_identifier="check_worker",
        target_identifier="worker",
        execution_status=ExecutionStatus.UNAVAILABLE,
        timestamp=timestamp,
    )

    assert observation.timestamp == timestamp


def test_belief_state_accepts_valid_distribution():
    state = BeliefState({"worker_ok": 0.7, "worker_failed": 0.3})

    assert state.probabilities["worker_ok"] == 0.7


def test_belief_state_rejects_invalid_distribution():
    with pytest.raises(ValueError):
        BeliefState({"worker_ok": 0.2, "worker_failed": 0.2})


def test_test_selection_creation():
    test = make_test()

    selection = ModelTestSelection(
        test=test,
        score=0.5,
        information_gain_bits=0.5,
        expected_energy_joules=1.0,
        expected_latency_seconds=0.5,
        policy="information_gain",
    )

    assert selection.test.identifier == "check_worker"
    assert selection.zero_cost_priority is False


def test_decision_values():
    assert Decision.CONTINUE.value == "CONTINUE"
    assert Decision.DIAGNOSED.value == "DIAGNOSED"
    assert Decision.ABSTAIN.value == "ABSTAIN"


def test_investigation_result_creation():
    result = InvestigationResult(
        decision=Decision.ABSTAIN,
        reason="Insufficient confidence",
        diagnosed_hypothesis=None,
        final_beliefs={"worker_ok": 0.5, "worker_failed": 0.5},
        belief_history=[
            BeliefState({"worker_ok": 0.5, "worker_failed": 0.5})
        ],
        observations=[],
        selections=[],
        total_estimated_energy_joules=0.0,
        total_estimated_latency_seconds=0.0,
    )

    assert result.decision == Decision.ABSTAIN
    assert result.diagnosed_hypothesis is None
