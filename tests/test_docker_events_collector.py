
from unittest.mock import Mock

import docker

from backend.collectors.docker_events import DockerEventsCollector


def make_collector(monkeypatch, events=None):
    fake_client = Mock()
    fake_client.events.return_value = events or []

    monkeypatch.setattr(
        "backend.collectors.docker_events.docker.from_env",
        lambda: fake_client
    )

    return DockerEventsCollector(), fake_client


def make_event(action, container_id="container123", timestamp=100):
    return {
        "Type": "container",
        "Action": action,
        "Actor": {
            "ID": container_id,
            "Attributes": {
                "name": "redis",
                "image": "redis:alpine",
            },
        },
        "time": timestamp,
        "timeNano": timestamp * 1_000_000_000,
    }


def test_collect_counts_events_and_detects_restart(monkeypatch):
    events = [
        make_event("die", timestamp=100),
        make_event("start", timestamp=110),
        make_event("oom", timestamp=120),
        make_event("restart", timestamp=130),
    ]

    collector, client = make_collector(monkeypatch, events)

    result = collector.collect(
        container_id="container123",
        lookback_seconds=60,
        event_actions=["die", "start", "oom", "restart"],
    )

    assert result["success"] is True
    assert result["event_count"] == 4
    assert result["action_counts"] == {
        "restart": 1,
        "die": 1,
        "oom": 1,
        "start": 1,
    }
    assert result["restart_count"] == 1
    assert result["die_count"] == 1
    assert result["oom_count"] == 1
    assert result["start_count"] == 1

    filters = client.events.call_args.kwargs["filters"]
    assert filters["type"] == "container"
    assert filters["container"] == "container123"
    assert filters["event"] == ["die", "start", "oom", "restart"]


def test_collect_normalizes_events(monkeypatch):
    collector, _ = make_collector(
        monkeypatch,
        [make_event("start")]
    )

    result = collector.collect()

    event = result["events"][0]

    assert event["type"] == "container"
    assert event["action"] == "start"
    assert event["actor_id"] == "container123"
    assert event["attributes"]["name"] == "redis"
    assert event["timestamp"] == 100


def test_collect_returns_empty_success_when_no_events(monkeypatch):
    collector, _ = make_collector(monkeypatch)

    result = collector.collect()

    assert result["success"] is True
    assert result["event_count"] == 0
    assert result["events"] == []
    assert result["restart_count"] == 0
    assert all(value == 0 for value in result["action_counts"].values())


def test_restart_detection_requires_die_then_start_for_same_container():
    collector = object.__new__(DockerEventsCollector)

    events = [
        {"actor_id": "A", "action": "start", "timestamp": 10},
        {"actor_id": "A", "action": "die", "timestamp": 20},
        {"actor_id": "B", "action": "start", "timestamp": 30},
        {"actor_id": "A", "action": "start", "timestamp": 40},
        {"actor_id": "B", "action": "die", "timestamp": 50},
        {"actor_id": "B", "action": "start", "timestamp": 60},
    ]

    assert collector._count_restarts(events) == 2


def test_collect_rejects_invalid_container_id(monkeypatch):
    collector, _ = make_collector(monkeypatch)

    result = collector.collect(container_id=" ")

    assert result["success"] is False
    assert result["error"] == "Invalid container name or ID"


def test_collect_rejects_invalid_lookback(monkeypatch):
    collector, _ = make_collector(monkeypatch)

    for value in (0, 3601, -1, 1.5, True):
        result = collector.collect(lookback_seconds=value)

        assert result["success"] is False
        assert "lookback_seconds" in result["error"]


def test_collect_rejects_invalid_since(monkeypatch):
    collector, _ = make_collector(monkeypatch)

    for value in (-1, 1.5, True, "100"):
        result = collector.collect(since=value)

        assert result["success"] is False
        assert "since must be a non-negative timestamp" in result["error"]


def test_collect_rejects_invalid_event_actions(monkeypatch):
    collector, _ = make_collector(monkeypatch)

    for value in ([], "start", ["start", ""], [1]):
        result = collector.collect(event_actions=value)

        assert result["success"] is False
        assert "event_actions must be a non-empty list" in result["error"]


def test_collect_rejects_future_timestamp(monkeypatch):
    collector, _ = make_collector(monkeypatch)

    result = collector.collect(since=9999999999)

    assert result["success"] is False
    assert result["error"] == "since cannot be in the future"


def test_collect_handles_unexpected_docker_error(monkeypatch):
    collector, client = make_collector(monkeypatch)
    client.events.side_effect = RuntimeError("Docker unavailable")

    result = collector.collect()

    assert result["success"] is False
    assert "Unexpected error" in result["error"]
    assert "Docker unavailable" in result["error"]
    assert result["event_count"] == 0
    assert result["events"] == []
