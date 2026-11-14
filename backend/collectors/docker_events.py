
import time
from datetime import datetime, timezone

import docker

from backend.collectors.base import BaseCollector


class DockerEventsCollector(BaseCollector):

    def __init__(self):
        self.client = docker.from_env()

    def collect(
        self,
        container_id=None,
        lookback_seconds=60,
        since=None,
        event_actions=None
    ):
        """
        Récupère les événements Docker sur une période passée.

        Args:
            container_id (str | None): Nom ou ID du conteneur.
            lookback_seconds (int): Durée historique en secondes.
            since (int | None): Timestamp Unix de début personnalisé.
            event_actions (list | None): Événements à filtrer.

        Returns:
            dict: Événements collectés et métadonnées.
        """

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
            return self._error("since must be a non-negative timestamp")

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

        filters = {"type": "container"}

        if container_id is not None:
            filters["container"] = container_id

        if event_actions is not None:
            filters["event"] = event_actions

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

            return {
                "source": "docker_events",
                "success": True,
                "collected_at": datetime.now(
                    timezone.utc
                ).isoformat(),
                "parameters": {
                    "container_id": container_id,
                    "lookback_seconds": lookback_seconds,
                    "since": since,
                    "until": now,
                    "event_actions": event_actions
                },
                "event_count": len(events),
                "events": events
            }

        except docker.errors.APIError as error:
            return self._error(f"Docker API error: {error}")

        except docker.errors.DockerException as error:
            return self._error(f"Docker connection error: {error}")

        except Exception as error:
            return self._error(f"Unexpected error: {error}")

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

    def _error(self, message):
        return {
            "source": "docker_events",
            "success": False,
            "error": message,
            "event_count": 0,
            "events": []
        }
