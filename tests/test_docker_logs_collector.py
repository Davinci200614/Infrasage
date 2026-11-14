
from unittest.mock import Mock

import docker

from backend.collectors.docker_logs import DockerLogsCollector


def make_collector(monkeypatch, container=None):
    fake_client = Mock()
    fake_client.containers.get.return_value = container

    monkeypatch.setattr(
        "backend.collectors.docker_logs.docker.from_env",
        lambda: fake_client
    )

    return DockerLogsCollector(), fake_client


def make_container():
    container = Mock()
    container.id = "container123"
    container.name = "redis"
    container.status = "running"
    container.image.tags = ["redis:alpine"]

    container.logs.side_effect = [
        b"Redis connection refused\nApplication started\n",
        b"level=error Redis timeout\n",
    ]

    return container


def test_collect_returns_container_logs(monkeypatch):
    container = make_container()
    collector, client = make_collector(monkeypatch, container)

    result = collector.collect("redis", tail=50)

    assert result["success"] is True
    assert result["container"]["name"] == "redis"
    assert result["container"]["status"] == "running"
    assert result["parameters"]["tail"] == 50
    assert "Redis connection refused" in result["logs"]["stdout"]
    assert "Redis timeout" in result["logs"]["stderr"]
    assert result["line_counts"]["stdout"] == 2
    assert result["line_counts"]["stderr"] == 1

    assert client.containers.get.call_args.args == ("redis",)
    assert container.reload.called
    assert container.logs.call_count == 2


def test_collect_detects_error_signatures():
    collector = object.__new__(DockerLogsCollector)

    result = collector._extract_error_signatures(
        stdout_text=(
            "Redis connection refused\n"
            "could not connect to server\n"
            "level=error something failed\n"
            "normal message\n"
        ),
        stderr_text=(
            "password authentication failed\n"
            "Traceback (most recent call last):\n"
        ),
    )

    assert result == {
        "redis_error_count": 1,
        "postgres_error_count": 2,
        "other_error_count": 2,
        "total_detected_signatures": 5,
    }


def test_collect_rejects_missing_container_id(monkeypatch):
    collector, _ = make_collector(monkeypatch)

    result = collector.collect("")

    assert result["success"] is False
    assert result["error"] == "A container name or ID is required"


def test_collect_rejects_invalid_tail(monkeypatch):
    collector, _ = make_collector(monkeypatch)

    for invalid_tail in (-1, 1.5, True, "10"):
        result = collector.collect("redis", tail=invalid_tail)

        assert result["success"] is False
        assert result["error"] == "tail must be a non-negative integer"


def test_collect_handles_container_not_found(monkeypatch):
    fake_client = Mock()
    fake_client.containers.get.side_effect = docker.errors.NotFound(
        "container not found"
    )

    monkeypatch.setattr(
        "backend.collectors.docker_logs.docker.from_env",
        lambda: fake_client
    )

    collector = DockerLogsCollector()
    result = collector.collect("missing-container")

    assert result["success"] is False
    assert "not found" in result["error"].lower()


def test_collect_handles_unexpected_error(monkeypatch):
    container = make_container()
    container.reload.side_effect = RuntimeError("unexpected failure")

    collector, _ = make_collector(monkeypatch, container)

    result = collector.collect("redis")

    assert result["success"] is False
    assert "Unexpected error" in result["error"]
    assert "unexpected failure" in result["error"]
