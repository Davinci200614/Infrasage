
import pytest

from backend.core.models import (
    DiagnosticTest,
    ExecutionStatus,
    Observation,
    Decision,
)
from backend.core.orchestrator import (
    run_investigation,
    StoppingConfig,
)
from backend.optimization.planner import DiagnosticPolicy


@pytest.fixture
def beliefs():
    return {
        "redis_unavailable": 0.5,
        "worker_stopped": 0.5,
    }


@pytest.fixture
def diagnostic_test():
    return DiagnosticTest(
        identifier="check_redis",
        outcomes=("reachable", "unreachable"),
        likelihoods={
            "redis_unavailable": {
                "reachable": 0.05,
                "unreachable": 0.95,
            },
            "worker_stopped": {
                "reachable": 0.95,
                "unreachable": 0.05,
            },
        },
        expected_energy_joules=5.0,
        expected_latency_seconds=2.0,
    )


def test_investigation_reaches_diagnosis(beliefs, diagnostic_test):
    observation = Observation(
        test_identifier="check_redis",
        target_identifier="synthetic-voting-app",
        execution_status=ExecutionStatus.COMPLETED,
        observed_outcome="unreachable",
        duration_seconds=2.0,
    )

    result = run_investigation(
        initial_beliefs=beliefs,
        tests=[diagnostic_test],
        policy=DiagnosticPolicy(method="information_gain"),
        predefined_observations={"check_redis": observation},
    )

    assert result.decision == Decision.DIAGNOSED
    assert result.diagnosed_hypothesis == "redis_unavailable"
    assert result.final_beliefs["redis_unavailable"] > 0.95


def test_maximum_test_count_causes_abstention(beliefs, diagnostic_test):
    result = run_investigation(
        initial_beliefs=beliefs,
        tests=[diagnostic_test],
        policy=DiagnosticPolicy(method="information_gain"),
        ground_truth="redis_unavailable",
        stopping=StoppingConfig(
            confidence_threshold=1.0,
            max_tests=0,
        ),
    )

    assert result.decision == Decision.ABSTAIN
    assert result.reason == "maximum test count reached"
    assert len(result.observations) == 0


def test_unavailable_observation_does_not_change_beliefs(
    beliefs, diagnostic_test
):
    observation = Observation(
        test_identifier="check_redis",
        target_identifier="synthetic-voting-app",
        execution_status=ExecutionStatus.UNAVAILABLE,
    )

    result = run_investigation(
        initial_beliefs=beliefs,
        tests=[diagnostic_test],
        policy=DiagnosticPolicy(method="information_gain"),
        predefined_observations={"check_redis": observation},
    )

    assert result.final_beliefs == beliefs
    assert result.observations[0].execution_status == ExecutionStatus.UNAVAILABLE
    assert result.total_estimated_energy_joules == 5.0
    assert result.total_estimated_latency_seconds == 2.0


def test_energy_and_latency_are_accumulated(beliefs, diagnostic_test):
    observation = Observation(
        test_identifier="check_redis",
        target_identifier="synthetic-voting-app",
        execution_status=ExecutionStatus.COMPLETED,
        observed_outcome="unreachable",
        duration_seconds=2.0,
    )

    result = run_investigation(
        initial_beliefs=beliefs,
        tests=[diagnostic_test],
        policy=DiagnosticPolicy(method="information_gain"),
        predefined_observations={"check_redis": observation},
    )

    assert result.total_estimated_energy_joules == 5.0
    assert result.total_estimated_latency_seconds == 2.0
    assert len(result.selections) == 1
