
from unittest.mock import Mock

import pytest

from backend.core.collection_orchestrator import CollectionOrchestrator


@pytest.fixture
def orchestrator():
    return CollectionOrchestrator()


# --------------------------------------------------
# INITIAL COLLECTION
# --------------------------------------------------

def test_collect_initial_returns_netdata_and_docker_results(orchestrator):
    netdata_result = {"cpu": {"usage": 20}}
    docker_result = [{"name": "redis", "running": True}]

    orchestrator.netdata_collector.collect = Mock(
        return_value=netdata_result
    )
    orchestrator.docker_collector.collect = Mock(
        return_value=docker_result
    )

    result = orchestrator.collect_initial()

    assert result == {
        "netdata": netdata_result,
        "docker": docker_result,
        "errors": {},
    }

    orchestrator.netdata_collector.collect.assert_called_once()
    orchestrator.docker_collector.collect.assert_called_once()


def test_collect_initial_handles_netdata_error(orchestrator):
    orchestrator.netdata_collector.collect = Mock(
        side_effect=RuntimeError("Netdata unavailable")
    )
    orchestrator.docker_collector.collect = Mock(
        return_value=[]
    )

    result = orchestrator.collect_initial()

    assert result["netdata"] is None
    assert result["docker"] == []
    assert result["errors"] == {
        "netdata": "Netdata unavailable"
    }


def test_collect_initial_handles_docker_error(orchestrator):
    orchestrator.netdata_collector.collect = Mock(
        return_value={"cpu": {}}
    )
    orchestrator.docker_collector.collect = Mock(
        side_effect=RuntimeError("Docker unavailable")
    )

    result = orchestrator.collect_initial()

    assert result["netdata"] == {"cpu": {}}
    assert result["docker"] is None
    assert result["errors"] == {
        "docker": "Docker unavailable"
    }


def test_collect_initial_handles_both_errors(orchestrator):
    orchestrator.netdata_collector.collect = Mock(
        side_effect=RuntimeError("Netdata failed")
    )
    orchestrator.docker_collector.collect = Mock(
        side_effect=RuntimeError("Docker failed")
    )

    result = orchestrator.collect_initial()

    assert result["netdata"] is None
    assert result["docker"] is None
    assert result["errors"] == {
        "netdata": "Netdata failed",
        "docker": "Docker failed",
    }


def test_collect_all_delegates_to_collect_initial(orchestrator):
    expected = {
        "netdata": {},
        "docker": [],
        "errors": {},
    }

    orchestrator.collect_initial = Mock(return_value=expected)

    result = orchestrator.collect_all()

    assert result == expected
    orchestrator.collect_initial.assert_called_once()


# --------------------------------------------------
# ON-DEMAND COLLECTION
# --------------------------------------------------

@pytest.mark.parametrize("collector_name", ["logs", "events"])
def test_collect_on_demand_executes_registered_collector(
    orchestrator,
    monkeypatch,
    collector_name,
):
    data = {"success": True, "items": []}
    collector_mock = Mock()
    collector_mock.return_value.collect.return_value = data

    monkeypatch.setitem(
        CollectionOrchestrator.ON_DEMAND_COLLECTORS,
        collector_name,
        collector_mock,
    )

    result = orchestrator.collect_on_demand(
        collector_name,
        container_name="redis",
    )

    assert result == {
        "collector": collector_name,
        "data": data,
        "errors": {},
    }

    collector_mock.assert_called_once_with()
    collector_mock.return_value.collect.assert_called_once_with(
        container_name="redis"
    )


def test_collect_on_demand_normalizes_collector_name(
    orchestrator,
    monkeypatch,
):
    data = {"success": True}

    collector_mock = Mock()
    collector_mock.return_value.collect.return_value = data

    monkeypatch.setitem(
        CollectionOrchestrator.ON_DEMAND_COLLECTORS,
        "logs",
        collector_mock,
    )

    result = orchestrator.collect_on_demand("  LOGS  ")

    assert result["collector"] == "logs"
    assert result["data"] == data
    assert result["errors"] == {}


@pytest.mark.parametrize(
    "collector_name",
    ["unknown", "metrics", "", None, 123],
)
def test_collect_on_demand_rejects_unknown_collector(
    orchestrator,
    collector_name,
):
    result = orchestrator.collect_on_demand(collector_name)

    normalized_name = (
        collector_name.strip().lower()
        if isinstance(collector_name, str)
        else ""
    )

    error_key = normalized_name or "unknown"

    assert result == {
        "collector": normalized_name,
        "data": None,
        "errors": {
            error_key: f"Unknown collector: {normalized_name}"
        },
    }


def test_collect_on_demand_reports_collector_failure(
    orchestrator,
    monkeypatch,
):
    data = {
        "success": False,
        "error": "Container not found",
    }

    collector_mock = Mock()
    collector_mock.return_value.collect.return_value = data

    monkeypatch.setitem(
        CollectionOrchestrator.ON_DEMAND_COLLECTORS,
        "logs",
        collector_mock,
    )

    result = orchestrator.collect_on_demand("logs")

    assert result["data"] == data
    assert result["errors"] == {
        "logs": "Container not found"
    }


def test_collect_on_demand_uses_default_error_message(
    orchestrator,
    monkeypatch,
):
    data = {"success": False}

    collector_mock = Mock()
    collector_mock.return_value.collect.return_value = data

    monkeypatch.setitem(
        CollectionOrchestrator.ON_DEMAND_COLLECTORS,
        "events",
        collector_mock,
    )

    result = orchestrator.collect_on_demand("events")

    assert result["errors"] == {
        "events": "Collection failed"
    }


def test_collect_on_demand_handles_collector_exception(
    orchestrator,
    monkeypatch,
):
    collector_mock = Mock()
    collector_mock.return_value.collect.side_effect = RuntimeError(
        "Docker API error"
    )

    monkeypatch.setitem(
        CollectionOrchestrator.ON_DEMAND_COLLECTORS,
        "logs",
        collector_mock,
    )

    result = orchestrator.collect_on_demand(
        "logs",
        container_name="redis",
    )

    assert result["data"] is None
    assert result["errors"] == {
        "logs": "Docker API error"
    }
