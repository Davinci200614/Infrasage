
import re
import docker
from datetime import datetime, timezone

from backend.collectors.base import BaseCollector


class DockerLogsCollector(BaseCollector):

    REDIS_PATTERNS = [
        r"redis.*connection refused",
        r"connection refused.*redis",
        r"redis.*timeout",
        r"timeout.*redis",
        r"could not connect to redis",
        r"redis.*unavailable",
        r"\bnoauth\b",
        r"\bwrongpass\b"
    ]

    POSTGRES_PATTERNS = [
        r"postgres.*connection refused",
        r"connection refused.*postgres",
        r"could not connect to server",
        r"too many connections",
        r"remaining connection slots are reserved",
        r"password authentication failed",
        r"database.*does not exist",
        r"postgres.*timeout",
        r"timeout.*postgres"
    ]

    OTHER_ERROR_PATTERNS = [
        r"\blevel\s*=\s*error\b",
        r"\blevel\s*=\s*fatal\b",
        r"\blevel\s*=\s*critical\b",
        r"\buncaught exception\b",
        r"\bunhandled exception\b",
        r"\btraceback \(most recent call last\):",
        r"\bsegmentation fault\b",
        r"\bpanic:"
    ]

    def __init__(self):
        self.client = docker.from_env()

    def collect(
        self,
        container_id,
        tail=100,
        since=None,
        until=None
    ):
        if not isinstance(container_id, str) or not container_id.strip():
            return {
                "source": "docker_logs",
                "success": False,
                "error": "A container name or ID is required"
            }

        if not isinstance(tail, int) or isinstance(tail, bool) or tail < 0:
            return {
                "source": "docker_logs",
                "success": False,
                "error": "tail must be a non-negative integer"
            }

        try:
            container = self.client.containers.get(container_id)
            container.reload()

            container_info = {
                "id": container.id,
                "name": container.name,
                "status": container.status,
                "image": container.image.tags
            }

            stdout = container.logs(
                stdout=True,
                stderr=False,
                tail=tail,
                since=since,
                until=until,
                timestamps=True,
                stream=False
            )

            stderr = container.logs(
                stdout=False,
                stderr=True,
                tail=tail,
                since=since,
                until=until,
                timestamps=True,
                stream=False
            )

            stdout_text = (
                stdout.decode("utf-8", errors="replace")
                if stdout else ""
            )

            stderr_text = (
                stderr.decode("utf-8", errors="replace")
                if stderr else ""
            )

            error_signatures = self._extract_error_signatures(
                stdout_text,
                stderr_text
            )

            return {
                "source": "docker_logs",
                "success": True,
                "container": container_info,
                "collected_at": datetime.now(timezone.utc).isoformat(),
                "parameters": {
                    "tail": tail,
                    "since": since,
                    "until": until
                },
                "logs": {
                    "stdout": stdout_text,
                    "stderr": stderr_text
                },
                "line_counts": {
                    "stdout": len(stdout_text.splitlines()),
                    "stderr": len(stderr_text.splitlines())
                },
                "error_signatures": error_signatures
            }

        except docker.errors.NotFound:
            return {
                "source": "docker_logs",
                "success": False,
                "error": f"Container '{container_id}' not found"
            }

        except docker.errors.APIError as error:
            return {
                "source": "docker_logs",
                "success": False,
                "error": f"Docker API error: {error}"
            }

        except docker.errors.DockerException as error:
            return {
                "source": "docker_logs",
                "success": False,
                "error": f"Docker connection error: {error}"
            }

        except Exception as error:
            return {
                "source": "docker_logs",
                "success": False,
                "error": f"Unexpected error: {error}"
            }

    def _extract_error_signatures(self, stdout_text, stderr_text):
        """
        Detect Redis, PostgreSQL and other significant errors.

        Each line is counted at most once.
        Redis and PostgreSQL matches have priority over generic errors.
        """

        lines = (
            f"{stdout_text}\n{stderr_text}"
        ).splitlines()

        signatures = {
            "redis_error_count": 0,
            "postgres_error_count": 0,
            "other_error_count": 0,
            "total_detected_signatures": 0
        }

        for line in lines:
            line = line.lower()

            redis_match = any(
                re.search(pattern, line)
                for pattern in self.REDIS_PATTERNS
            )

            postgres_match = any(
                re.search(pattern, line)
                for pattern in self.POSTGRES_PATTERNS
            )

            other_match = any(
                re.search(pattern, line)
                for pattern in self.OTHER_ERROR_PATTERNS
            )

            if redis_match:
                signatures["redis_error_count"] += 1

            elif postgres_match:
                signatures["postgres_error_count"] += 1

            elif other_match:
                signatures["other_error_count"] += 1

        signatures["total_detected_signatures"] = (
            signatures["redis_error_count"]
            + signatures["postgres_error_count"]
            + signatures["other_error_count"]
        )

        return signatures
