
from unittest.mock import Mock, patch

from backend.collectors.docker import DockerCollector


def make_container(labels=None):
    """Construit un faux conteneur Docker avec les attributs nécessaires."""

    container = Mock()

    container.id = "abc123"
    container.name = "infrasage-redis-1"
    container.image.tags = ["redis:alpine"]

    container.attrs = {
        "State": {
            "Status": "exited",
            "Running": False,
            "Paused": False,
            "Restarting": False,
            "Dead": False,
            "ExitCode": 0,
            "Error": "",
            "StartedAt": "2026-11-14T10:00:00Z",
            "FinishedAt": "2026-11-14T11:00:00Z",
        },
        "Config": {
            "Labels": labels,
            "Healthcheck": None,
        },
        "NetworkSettings": {
            "Ports": {},
            "Networks": {},
        },
        "RestartCount": 0,
    }

    return container


@patch("backend.collectors.docker.docker.from_env")
def test_collect_extracts_docker_compose_labels(mock_from_env):
    container = make_container({
        "com.docker.compose.service": "redis",
        "com.docker.compose.project": "voting-app",
    })

    client = Mock()
    client.containers.list.return_value = [container]
    mock_from_env.return_value = client

    collector = DockerCollector()
    results = collector.collect()

    assert len(results) == 1

    result = results[0]

    assert result["compose_service"] == "redis"
    assert result["compose_project"] == "voting-app"

    assert result["labels"] == {
        "com.docker.compose.service": "redis",
        "com.docker.compose.project": "voting-app",
    }

    container.reload.assert_called_once()


@patch("backend.collectors.docker.docker.from_env")
def test_collect_handles_missing_compose_labels(mock_from_env):
    container = make_container(labels=None)

    client = Mock()
    client.containers.list.return_value = [container]
    mock_from_env.return_value = client

    collector = DockerCollector()
    results = collector.collect()

    assert len(results) == 1

    result = results[0]

    assert result["labels"] == {}
    assert result["compose_service"] is None
    assert result["compose_project"] is None
