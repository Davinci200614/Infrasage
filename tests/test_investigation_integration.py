
import pytest
from unittest.mock import Mock

from backend.core.models import (
    DiagnosticTest,
    ExecutionStatus,
)
from backend.core.collector_manager import CollectorManager
from backend.core.diagnostic_adapter import DiagnosticAdapter
from backend.diagnosis.bayesian import apply_observation
from backend.optimization.planner import DiagnosticPolicy


def make_test():
    return DiagnosticTest(
        identifier="redis_runtime_state",
        outcomes=("running", "stopped"),
        likelihoods={
            "redis_unavailable": {
                "running": 0.05,
                "stopped": 0.95,
            },
            "worker_stopped": {
                "running": 0.95,
                "stopped": 0.05,
            },
        },
        expected_energy_joules=5.0,
        expected_latency_seconds=2.0,
    )


def test_collector_adapter_bayesian_integration():
    # 1. Initial beliefs
    beliefs = {
        "redis_unavailable": 0.5,
        "worker_stopped": 0.5,
    }

    test = make_test()

    # 2. Planner selects the diagnostic test
    planner = DiagnosticPolicy(method="information_gain")
    selection = planner.select(beliefs, [test])

    assert selection.test.identifier == "redis_runtime_state"

    # 3. Simulate CollectorManager
    manager = Mock(spec=CollectorManager)

    manager.execute_test.return_value = {
        "test_id": "redis_runtime_state",
        "status": "completed",
        "data": {
            "running": False,
        },
        "duration_seconds": 1.5,
    }

    target = {
        "container_name": "redis",
    }

    execution_result = manager.execute_test(selection.test, target)

    # 4. Real DiagnosticAdapter
    adapter = DiagnosticAdapter(
        outcome_mappings={
            "redis_runtime_state": {
                "type": "field",
                "field": "running",
                "values": {
                    "True": "running",
                    "False": "stopped",
                },
            }
        }
    )

    observation = adapter.adapt(
        selection.test,
        execution_result,
        target,
    )

    # 5. Verify normalized observation
    assert observation.test_identifier == "redis_runtime_state"
    assert observation.target_identifier == "redis"
    assert observation.execution_status == ExecutionStatus.COMPLETED
    assert observation.observed_outcome == "stopped"

    # 6. Bayesian update
    updated_beliefs = apply_observation(
        beliefs,
        test,
        observation,
    )

    assert updated_beliefs["redis_unavailable"] == pytest.approx(0.95)
    assert updated_beliefs["worker_stopped"] == pytest.approx(0.05)


def test_unavailable_execution_does_not_update_beliefs():
    beliefs = {
        "redis_unavailable": 0.5,
        "worker_stopped": 0.5,
    }

    test = make_test()

    manager = Mock(spec=CollectorManager)
    manager.execute_test.return_value = {
        "test_id": "redis_runtime_state",
        "status": "unavailable",
        "data": None,
        "error": "Live execution disabled",
    }

    target = {"container_name": "redis"}

    result = manager.execute_test(test, target)

    adapter = DiagnosticAdapter()
    observation = adapter.adapt(test, result, target)

    updated_beliefs = apply_observation(
        beliefs,
        test,
        observation,
    )

    assert observation.execution_status == ExecutionStatus.UNAVAILABLE
    assert updated_beliefs == beliefs

def test_run_investigation_collector_mode():
    from backend.core.orchestrator import (
        run_investigation,
        StoppingConfig,
    )

    beliefs = {
        "redis_unavailable": 0.5,
        "worker_stopped": 0.5,
    }

    test = make_test()

    manager = Mock(spec=CollectorManager)

    manager.execute_test.return_value = {
        "test_id": "redis_runtime_state",
        "status": "completed",
        "data": {
            "running": False,
        },
        "duration_seconds": 1.5,
    }

    adapter = DiagnosticAdapter(
        outcome_mappings={
            "redis_runtime_state": {
                "type": "field",
                "field": "running",
                "values": {
                    "True": "running",
                    "False": "stopped",
                },
            }
        }
    )

    target = {
        "container_name": "redis",
    }

    result = run_investigation(
        initial_beliefs=beliefs,
        tests=[test],
        policy=DiagnosticPolicy(
            method="information_gain"
        ),
        stopping=StoppingConfig(
            confidence_threshold=0.95,
            max_tests=1,
        ),
        collector_manager=manager,
        diagnostic_adapter=adapter,
        target=target,
    )

    assert result.decision.value == "DIAGNOSED"
    assert result.diagnosed_hypothesis == "redis_unavailable"
    assert len(result.observations) == 1
    assert result.observations[0].observed_outcome == "stopped"
    assert result.final_beliefs["redis_unavailable"] == pytest.approx(0.95)

    manager.execute_test.assert_called_once_with(
        test,
        target,
    )


def test_run_investigation_collector_unavailable():
    from backend.core.orchestrator import (
        run_investigation,
        StoppingConfig,
    )

    beliefs = {
        "redis_unavailable": 0.5,
        "worker_stopped": 0.5,
    }

    test = make_test()

    manager = Mock(spec=CollectorManager)

    manager.execute_test.return_value = {
        "test_id": "redis_runtime_state",
        "status": "unavailable",
        "data": None,
        "error": "Live execution disabled",
    }

    adapter = DiagnosticAdapter()

    target = {
        "container_name": "redis",
    }

    result = run_investigation(
        initial_beliefs=beliefs,
        tests=[test],
        policy=DiagnosticPolicy(
            method="information_gain"
        ),
        stopping=StoppingConfig(
            max_tests=1,
        ),
        collector_manager=manager,
        diagnostic_adapter=adapter,
        target=target,
    )

    assert result.decision.value == "ABSTAIN"
    assert result.observations[0].execution_status == ExecutionStatus.UNAVAILABLE
    assert result.final_beliefs == beliefs
    assert len(result.observations) == 1

    manager.execute_test.assert_called_once_with(
        test,
        target,
    )
