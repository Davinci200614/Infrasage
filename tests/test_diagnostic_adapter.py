
import pytest

from backend.core.models import DiagnosticTest, ExecutionStatus
from backend.core.diagnostic_adapter import DiagnosticAdapter


@pytest.fixture
def diagnostic_test():
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


@pytest.fixture
def adapter():
    return DiagnosticAdapter(
        outcome_mappings={
            "redis_runtime_state": {
                "type": "field",
                "field": "running",
                "values": {
                    "True": "running",
                    "False": "stopped",
                },
            },
            "redis_restarts": {
                "type": "restart_activity",
                "repeated_threshold": 3,
            },
            "worker_logs": {
                "type": "worker_error_signature",
            },
        }
    )


def test_adapt_completed_execution(adapter, diagnostic_test):
    result = adapter.adapt(
        diagnostic_test,
        {
            "status": "completed",
            "data": {"running": False},
            "duration_seconds": 1.5,
            "measured_energy_joules": 2.0,
        },
        {"container_name": "redis"},
    )

    assert result.execution_status == ExecutionStatus.COMPLETED
    assert result.observed_outcome == "stopped"
    assert result.target_identifier == "redis"
    assert result.duration_seconds == 1.5
    assert result.measured_energy_joules == 2.0


def test_adapt_failed_execution(adapter, diagnostic_test):
    result = adapter.adapt(
        diagnostic_test,
        {"status": "failed", "error": "Docker error"},
        {"container_name": "redis"},
    )

    assert result.execution_status == ExecutionStatus.FAILED
    assert result.observed_outcome is None


def test_adapt_unavailable_execution(adapter, diagnostic_test):
    result = adapter.adapt(
        diagnostic_test,
        {"status": "unavailable", "error": "Not available"},
        {"container_name": "redis"},
    )

    assert result.execution_status == ExecutionStatus.UNAVAILABLE
    assert result.observed_outcome is None


def test_adapt_unknown_status_as_unavailable(adapter, diagnostic_test):
    result = adapter.adapt(
        diagnostic_test,
        {"status": "unknown"},
        {"container_name": "redis"},
    )

    assert result.execution_status == ExecutionStatus.UNAVAILABLE


def test_adapt_completed_without_dictionary_data(adapter, diagnostic_test):
    result = adapter.adapt(
        diagnostic_test,
        {"status": "completed", "data": None},
        {"container_name": "redis"},
    )

    assert result.execution_status == ExecutionStatus.UNAVAILABLE


def test_adapt_unknown_outcome_as_unavailable(adapter, diagnostic_test):
    result = adapter.adapt(
        diagnostic_test,
        {"status": "completed", "data": {"running": "unknown"}},
        {"container_name": "redis"},
    )

    assert result.execution_status == ExecutionStatus.UNAVAILABLE
    assert result.observed_outcome is None


def test_adapt_rejects_invalid_arguments(adapter, diagnostic_test):
    with pytest.raises(TypeError):
        adapter.adapt(
            {},
            {"status": "completed", "data": {}},
            {},
        )

    with pytest.raises(TypeError):
        adapter.adapt(
            diagnostic_test,
            None,
            {},
        )

    with pytest.raises(TypeError):
        adapter.adapt(
            diagnostic_test,
            {"status": "completed", "data": {}},
            None,
        )


def test_field_mapping_supports_nested_values():
    adapter = DiagnosticAdapter({
        "redis_runtime_state": {
            "type": "field",
            "field": "container.state.running",
            "values": {"True": "running"},
        }
    })

    result = adapter._resolve_outcome(
        "redis_runtime_state",
        {"container": {"state": {"running": True}}},
    )

    assert result == "running"


def test_field_mapping_returns_none_for_missing_field():
    adapter = DiagnosticAdapter({
        "redis_runtime_state": {
            "type": "field",
            "field": "container.running",
            "values": {"True": "running"},
        }
    })

    assert adapter._resolve_outcome(
        "redis_runtime_state",
        {},
    ) is None


def test_restart_activity_mapping():
    adapter = DiagnosticAdapter({
        "redis_restarts": {
            "type": "restart_activity",
            "repeated_threshold": 3,
        }
    })

    assert adapter._resolve_outcome(
        "redis_restarts", {"restart_count": 0}
    ) == "none"

    assert adapter._resolve_outcome(
        "redis_restarts", {"restart_count": 1}
    ) == "recent"

    assert adapter._resolve_outcome(
        "redis_restarts", {"restart_count": 3}
    ) == "repeated"


@pytest.mark.parametrize("count", [-1, True, 1.5, "2", None])
def test_restart_activity_rejects_invalid_count(count):
    adapter = DiagnosticAdapter({
        "redis_restarts": {"type": "restart_activity"}
    })

    assert adapter._resolve_outcome(
        "redis_restarts",
        {"restart_count": count},
    ) is None


@pytest.mark.parametrize("threshold", [0, 1, -1, True, 2.5])
def test_restart_activity_rejects_invalid_threshold(threshold):
    adapter = DiagnosticAdapter({
        "redis_restarts": {
            "type": "restart_activity",
            "repeated_threshold": threshold,
        }
    })

    assert adapter._resolve_outcome(
        "redis_restarts",
        {"restart_count": 1},
    ) is None


def test_worker_error_signature_mapping():
    adapter = DiagnosticAdapter({
        "worker_logs": {"type": "worker_error_signature"}
    })

    assert adapter._resolve_outcome(
        "worker_logs",
        {"error_signatures": {
            "redis_error_count": 2,
            "postgres_error_count": 0,
            "other_error_count": 0,
        }},
    ) == "redis_error"

    assert adapter._resolve_outcome(
        "worker_logs",
        {"error_signatures": {
            "redis_error_count": 0,
            "postgres_error_count": 1,
            "other_error_count": 0,
        }},
    ) == "postgres_error"

    assert adapter._resolve_outcome(
        "worker_logs",
        {"error_signatures": {
            "redis_error_count": 0,
            "postgres_error_count": 0,
            "other_error_count": 0,
        }},
    ) == "none"


def test_worker_error_signature_avoids_ambiguous_result():
    adapter = DiagnosticAdapter({
        "worker_logs": {"type": "worker_error_signature"}
    })

    result = adapter._resolve_outcome(
        "worker_logs",
        {"error_signatures": {
            "redis_error_count": 1,
            "postgres_error_count": 1,
            "other_error_count": 0,
        }},
    )

    assert result is None


def test_worker_error_signature_rejects_invalid_counters():
    adapter = DiagnosticAdapter({
        "worker_logs": {"type": "worker_error_signature"}
    })

    for invalid_count in [-1, True, 1.5, "2", None]:
        result = adapter._resolve_outcome(
            "worker_logs",
            {"error_signatures": {
                "redis_error_count": invalid_count,
                "postgres_error_count": 0,
                "other_error_count": 0,
            }},
        )
        assert result is None


def test_target_identifier_priority(adapter, diagnostic_test):
    result = adapter.adapt(
        diagnostic_test,
        {"status": "unavailable"},
        {
            "container_name": "redis",
            "container_id": "abc123",
            "service": "cache",
        },
    )

    assert result.target_identifier == "redis"


def test_target_identifier_fallback(adapter, diagnostic_test):
    result = adapter.adapt(
        diagnostic_test,
        {"status": "unavailable"},
        {"service": "redis"},
    )

    assert result.target_identifier == "redis"


def test_target_identifier_defaults_to_infrastructure(
    adapter, diagnostic_test
):
    result = adapter.adapt(
        diagnostic_test,
        {"status": "unavailable"},
        {},
    )

    assert result.target_identifier == "infrastructure"
