
from types import SimpleNamespace
from unittest.mock import Mock

from backend.core.collector_manager import CollectorManager


def make_catalogue():
    return SimpleNamespace(
        research_mode=False,
        test_metadata={
            "redis_runtime_state": {
                "provider": "docker",
                "execution": {
                    "logical_operation": "container_process_state",
                    "binding_status": "verified",
                    "live_binding": {
                        "operation": "container_process_state",
                    },
                },
                "availability": {
                    "live": True,
                },
            }
        },
    )


def make_manager(docker_result=None, docker_error=None):
    collection_orchestrator = Mock()

    if docker_error is not None:
        collection_orchestrator.collect_initial.return_value = {
            "netdata": None,
            "docker": None,
            "errors": {
                "docker": str(docker_error),
            },
        }
    else:
        collection_orchestrator.collect_initial.return_value = {
            "netdata": {},
            "docker": docker_result,
            "errors": {},
        }

    manager = CollectorManager(
        catalogue=make_catalogue(),
        collection_orchestrator=collection_orchestrator,
    )

    return manager, collection_orchestrator


# --------------------------------------------------
# EXPLICIT CONTAINER IDENTIFIER
# --------------------------------------------------

def test_runtime_state_returns_matching_container():
    containers = [
        {
            "id": "abc123",
            "name": "redis",
            "running": False,
            "restarting": False,
            "dead": False,
        },
        {
            "id": "def456",
            "name": "worker",
            "running": True,
            "restarting": False,
            "dead": False,
        },
    ]

    manager, orchestrator = make_manager(
        docker_result=containers
    )

    result = manager.execute_test(
        "redis_runtime_state",
        {"container_name": "redis"},
    )

    assert result["status"] == "completed"
    assert result["test_id"] == "redis_runtime_state"
    assert result["data"]["id"] == "abc123"
    assert result["data"]["name"] == "redis"

    orchestrator.collect_initial.assert_called_once()


def test_runtime_state_returns_unavailable_when_container_not_found():
    manager, _ = make_manager(
        docker_result=[
            {
                "id": "abc123",
                "name": "redis",
                "running": True,
            }
        ]
    )

    result = manager.execute_test(
        "redis_runtime_state",
        {"container_name": "postgres"},
    )

    assert result["status"] == "unavailable"
    assert "Container not found" in result["error"]


def test_runtime_state_requires_container_identifier():
    manager, orchestrator = make_manager(
        docker_result=[]
    )

    result = manager.execute_test(
        "redis_runtime_state",
        {},
    )

    assert result["status"] == "unavailable"
    assert result["error"] == "Container identifier is required"

    orchestrator.collect_initial.assert_not_called()


def test_runtime_state_handles_docker_collection_error():
    manager, _ = make_manager(
        docker_error=RuntimeError("Docker connection failed")
    )

    result = manager.execute_test(
        "redis_runtime_state",
        {"container_name": "redis"},
    )

    assert result["status"] == "failed"
    assert result["error"] == "Docker connection failed"


# --------------------------------------------------
# EXECUTION SECURITY
# --------------------------------------------------

def test_research_mode_blocks_live_execution():
    catalogue = make_catalogue()
    catalogue.research_mode = True

    orchestrator = Mock()

    manager = CollectorManager(
        catalogue=catalogue,
        collection_orchestrator=orchestrator,
    )

    result = manager.execute_test(
        "redis_runtime_state",
        {"container_name": "redis"},
    )

    assert result["status"] == "unavailable"
    assert result["error"] == (
        "Live diagnostic execution is disabled in Phase 1"
    )

    orchestrator.collect_initial.assert_not_called()


def test_unverified_binding_blocks_execution():
    catalogue = make_catalogue()

    catalogue.test_metadata["redis_runtime_state"][
        "execution"
    ]["binding_status"] = "assumed_unverified"

    orchestrator = Mock()

    manager = CollectorManager(
        catalogue=catalogue,
        collection_orchestrator=orchestrator,
    )

    result = manager.execute_test(
        "redis_runtime_state",
        {"container_name": "redis"},
    )

    assert result["status"] == "unavailable"
    assert result["error"] == (
        "Test binding has not been verified"
    )

    orchestrator.collect_initial.assert_not_called()


# --------------------------------------------------
# DOCKER COMPOSE SERVICE RESOLUTION
# --------------------------------------------------

def test_runtime_state_resolves_container_by_compose_service():
    containers = [
        {
            "id": "abc123",
            "name": "voting-redis-1",
            "compose_service": "redis",
            "running": True,
            "restarting": False,
            "dead": False,
        },
        {
            "id": "def456",
            "name": "voting-worker-1",
            "compose_service": "worker",
            "running": True,
            "restarting": False,
            "dead": False,
        },
    ]

    catalogue = make_catalogue()

    catalogue.test_metadata["redis_runtime_state"][
        "target_selector"
    ] = {
        "application": "voting-app",
        "component": "redis",
    }

    orchestrator = Mock()

    orchestrator.collect_initial.return_value = {
        "netdata": {},
        "docker": containers,
        "errors": {},
    }

    manager = CollectorManager(
        catalogue=catalogue,
        collection_orchestrator=orchestrator,
    )

    result = manager.execute_test(
        "redis_runtime_state",
        {},
    )

    assert result["status"] == "completed"
    assert result["data"]["id"] == "abc123"
    assert result["data"]["name"] == "voting-redis-1"
    assert result["data"]["compose_service"] == "redis"

    orchestrator.collect_initial.assert_called_once()


def test_runtime_state_returns_unavailable_when_compose_service_is_ambiguous():
    containers = [
        {
            "id": "abc123",
            "name": "voting-redis-1",
            "compose_service": "redis",
            "running": True,
        },
        {
            "id": "def456",
            "name": "another-redis-1",
            "compose_service": "redis",
            "running": True,
        },
    ]

    catalogue = make_catalogue()

    catalogue.test_metadata["redis_runtime_state"][
        "target_selector"
    ] = {
        "application": "voting-app",
        "component": "redis",
    }

    orchestrator = Mock()

    orchestrator.collect_initial.return_value = {
        "netdata": {},
        "docker": containers,
        "errors": {},
    }

    manager = CollectorManager(
        catalogue=catalogue,
        collection_orchestrator=orchestrator,
    )

    result = manager.execute_test(
        "redis_runtime_state",
        {},
    )

    assert result["status"] == "unavailable"
    assert result["error"] == "Multiple containers match: redis"

    orchestrator.collect_initial.assert_called_once()
