
import pytest

from backend.core.models import Decision, ExecutionStatus
from backend.core.orchestrator import (
    StoppingConfig,
    run_investigation,
)
from backend.optimization.planner import DiagnosticPolicy


def test_complete_simulated_investigation():
    # 1. Hypothèses initiales
    initial_beliefs = {
        "redis_unavailable": 0.5,
        "worker_stopped": 0.5,
    }

    # 2. Test diagnostique
    from backend.core.models import DiagnosticTest

    diagnostic_test = DiagnosticTest(
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

    # 3. Politique de sélection
    policy = DiagnosticPolicy(
        method="information_gain"
    )

    # 4. Configuration d'arrêt
    stopping = StoppingConfig(
        confidence_threshold=0.95,
        max_tests=1,
        max_energy_joules=100.0,
        minimum_successful_observations=1,
    )

    # 5. Exécution complète en simulation
    result = run_investigation(
        initial_beliefs=initial_beliefs,
        tests=[diagnostic_test],
        policy=policy,
        ground_truth="redis_unavailable",
        seed=0,
        stopping=stopping,
    )

    # 6. Vérification de la sélection
    assert len(result.selections) == 1
    assert result.selections[0].test.identifier == "check_redis"

    # 7. Vérification de l'observation simulée
    assert len(result.observations) == 1

    observation = result.observations[0]

    assert observation.execution_status == ExecutionStatus.COMPLETED
    assert observation.observed_outcome == "unreachable"

    # 8. Vérification de la mise à jour bayésienne
    assert result.final_beliefs["redis_unavailable"] == pytest.approx(0.95)
    assert result.final_beliefs["worker_stopped"] == pytest.approx(0.05)

    # 9. Vérification de la décision finale
    assert result.decision == Decision.DIAGNOSED
    assert result.diagnosed_hypothesis == "redis_unavailable"

    # 10. Vérification de l'historique et des coûts
    assert len(result.belief_history) == 2

    assert result.total_estimated_energy_joules == 5.0
    assert result.total_estimated_latency_seconds == 2.0
