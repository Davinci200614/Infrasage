
import time
from datetime import datetime, timezone

import docker

from backend.collectors.base import BaseCollector


class DockerEventsCollector(BaseCollector):

    IMPORTANT_ACTIONS = ("restart", "die", "oom", "start")

    def __init__(self):
        self.client = docker.from_env()

    def collect(
        self,
        container_id=None,
        lookback_seconds=60,
        since=None,
        event_actions=None
    ):

        # 1. Validate parameters

        if container_id is not None and (
            not isinstance(container_id, str)
            or not container_id.strip()
        ):
            return self._error("Invalid container name or ID")

        if (
            not isinstance(lookback_seconds, int)
            or isinstance(lookback_seconds, bool)
            or not 1 <= lookback_seconds <= 3600
        ):
            return self._error(
                "lookback_seconds must be between 1 and 3600"
            )

        if since is not None and (
            not isinstance(since, int)
            or isinstance(since, bool)
            or since < 0
        ):
            return self._error(
                "since must be a non-negative timestamp"
            )

        if event_actions is not None and (
            not isinstance(event_actions, list)
            or not event_actions
            or any(
                not isinstance(action, str) or not action.strip()
                for action in event_actions
            )
        ):
            return self._error(
                "event_actions must be a non-empty list of strings"
            )

        now = int(time.time())

        if since is None:
            since = now - lookback_seconds

        if since > now:
            return self._error("since cannot be in the future")

        # 2. Build Docker event filters

        filters = {"type": "container"}

        if container_id is not None:
            filters["container"] = container_id

        if event_actions is not None:
            filters["event"] = event_actions

        # 3. Collect and normalize events

        try:
            raw_events = self.client.events(
                decode=True,
                filters=filters,
                since=since,
                until=now
            )

            events = [
                self._normalize_event(event)
                for event in raw_events
            ]

            # 4. Count important actions

            action_counts = {
                action: 0
                for action in self.IMPORTANT_ACTIONS
            }

            for event in events:
                action = event.get("action")

                if action in action_counts:
                    action_counts[action] += 1

            # 5. Detect restart sequences

            restart_count = self._count_restarts(events)

            return {
                "source": "docker_events",
                "success": True,
                "collected_at": datetime.now(timezone.utc).isoformat(),

                "parameters": {
                    "container_id": container_id,
                    "lookback_seconds": lookback_seconds,
                    "since": since,
                    "until": now,
                    "event_actions": event_actions
                },

                # Original fields preserved
                "event_count": len(events),
                "events": events,

                # Action statistics
                "action_counts": action_counts,
                "restart_count": restart_count,
                "die_count": action_counts["die"],
                "oom_count": action_counts["oom"],
                "start_count": action_counts["start"]
            }

        except docker.errors.APIError as error:
            return self._error(f"Docker API error: {error}")

        except docker.errors.DockerException as error:
            return self._error(f"Docker connection error: {error}")

        except Exception as error:
            return self._error(f"Unexpected error: {error}")

    # --------------------------------------------------
    # EVENT NORMALIZATION
    # --------------------------------------------------

    def _normalize_event(self, event):

        actor = event.get("Actor") or {}

        return {
            "type": event.get("Type"),
            "action": event.get("Action"),
            "actor_id": actor.get("ID"),
            "attributes": actor.get("Attributes", {}),
            "timestamp": event.get("time"),
            "timestamp_nano": event.get("timeNano")
        }

    # --------------------------------------------------
    # RESTART DETECTION
    # --------------------------------------------------

    def _count_restarts(self, events):
        """
        Count restart sequences: die followed by start
        for the same container.

        Events must be chronologically ordered.
        """

        last_actions = {}
        restart_count = 0

        sorted_events = sorted(
            events,
            key=lambda event: (
                event.get("timestamp")
                if isinstance(event.get("timestamp"), (int, float))
                else 0
            )
        )

        for event in sorted_events:

            container_id = event.get("actor_id")
            action = event.get("action")

            if not container_id:
                continue

            previous_action = last_actions.get(container_id)

            if previous_action == "die" and action == "start":
                restart_count += 1

            last_actions[container_id] = action

        return restart_count

    # --------------------------------------------------
    # ERROR HANDLING
    # --------------------------------------------------

    def _error(self, message):

        return {
            "source": "docker_events",
            "success": False,
            "error": message,
            "event_count": 0,
            "events": [],
            "action_counts": {
                action: 0
                for action in self.IMPORTANT_ACTIONS
            },
            "restart_count": 0,
            "die_count": 0,
            "oom_count": 0,
            "start_count": 0
        }
