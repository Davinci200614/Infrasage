from datetime import datetime, timezone
from time import perf_counter
import re


def valid_container_id(value):
    return isinstance(value, str) and re.fullmatch(r'[0-9a-f]{64}', value) is not None


def valid_container_name(value):
    return isinstance(value, str) and re.fullmatch(r'[a-zA-Z0-9][a-zA-Z0-9_.-]*', value) is not None


import docker
from backend.collectors.base import BaseCollector


class DockerCollector(BaseCollector):

    def __init__(self):
        self.client = docker.from_env()

    def collect(self):
        containers = self.client.containers.list(all=True)

        results = []

        for container in containers:
            try:
                container.reload()
                attrs = container.attrs

                state = attrs.get("State", {})
                memory_health = state.get("Health", {})

                image_tags = container.image.tags

                data = {
                    "id": container.id,
                    "name": container.name,
                    "image": image_tags,
                    "status": state.get("Status"),
                    "health": memory_health.get("Status"),
                    "ports": attrs.get(
                        "NetworkSettings", {}
                    ).get("Ports") or {},
                    "restart_count": attrs.get("RestartCount", 0),
                    "cpu_percent": None,
                    "memory_usage_mb": None,
                    "memory_limit_mb": None
                }

                if data["status"] == "running":
                    stats = container.stats(stream=False)

                    data["cpu_percent"] = self.calculate_cpu_percent(stats)

                    memory_stats = stats["memory_stats"]
                    usage = memory_stats["usage"]
                    memory_details = memory_stats.get("stats", {})

                    cache = memory_details.get(
                        "total_inactive_file",
                        memory_details.get(
                            "inactive_file",
                            memory_details.get("cache", 0)
                        )
                    )

                    memory_used = max(usage - cache, 0)

                    data["memory_usage_mb"] = memory_used / (1024 ** 2)

                    data["memory_limit_mb"] = (
                        memory_stats["limit"] / (1024 ** 2)
                    )

                results.append(data)

            except docker.errors.APIError:
                # Le conteneur peut avoir disparu ou être devenu inaccessible.
                continue

        return results

    def calculate_cpu_percent(self, stats):
        cpu_stats = stats["cpu_stats"]
        precpu_stats = stats["precpu_stats"]

        cpu_delta = (
            cpu_stats["cpu_usage"]["total_usage"]
            - precpu_stats["cpu_usage"]["total_usage"]
        )

        system_delta = (
            cpu_stats["system_cpu_usage"]
            - precpu_stats["system_cpu_usage"]
        )

        number_cpus = cpu_stats.get("online_cpus", 1)

        if system_delta > 0 and cpu_delta >= 0:
            return (cpu_delta / system_delta) * number_cpus * 100

        return 0.0

    def inspect_runtime(self, *, container_id=None, container_name=None):
        """Inspect one exact ID/name; never retrieve stats, logs or other containers.

        Short ID prefixes are deliberately unsupported: callers must supply the full
        64-character ID or an exact Docker name. Both, when supplied, must agree.
        """
        start = perf_counter()
        result = {"source": "docker", "status": "unavailable", "data": None,
                  "target": None, "observed_at": None, "retrieved_at": None,
                  "docker_endpoint": self.client.api.base_url}
        try:
            if ((container_id is None and container_name is None)
                    or (container_id is not None and not valid_container_id(container_id))
                    or (container_name is not None and not valid_container_name(container_name))):
                result["error"] = "An exact container name or full container ID is required; prefixes are ambiguous"
                return result
            container = self.client.containers.get(container_id or container_name)
            attrs = container.attrs  # get() has already performed inspect_container.
            actual_id = attrs.get("Id")
            raw_name = attrs.get("Name")
            actual_name = raw_name.removeprefix("/") if isinstance(raw_name, str) else None
            if not valid_container_id(actual_id) or not valid_container_name(actual_name):
                result.update(status="failed", error="Malformed Docker identity")
                return result
            if ((container_id is not None and container_id != actual_id)
                    or (container_name is not None and container_name != actual_name)):
                result["error"] = "Container identity mismatch or ambiguous prefix"
                return result
            state = attrs.get("State")
            if not isinstance(state, dict) or not isinstance(state.get("Status"), str):
                result.update(status="failed", error="Malformed Docker runtime state")
                return result
            observed = datetime.now(timezone.utc).isoformat()
            result.update(
                status="completed", target={"container_id": actual_id, "container_name": actual_name},
                target_identifier="docker:" + actual_id, observed_at=observed,
                data={"id": actual_id, "name": actual_name, "status": state["Status"],
                      "running": state.get("Running"), "restarting": state.get("Restarting"),
                      "paused": state.get("Paused"), "dead": state.get("Dead"),
                      "exit_code": state.get("ExitCode"), "state_error": state.get("Error"),
                      "started_at": state.get("StartedAt"), "finished_at": state.get("FinishedAt"),
                      "image": attrs.get("Config", {}).get("Image")})
            return result
        except docker.errors.NotFound as error:
            result["error"] = f"Container not found: {error}"
            return result
        except Exception as error:
            result.update(status="failed", error=f"Docker inspection failed: {error}")
            return result
        finally:
            result["retrieved_at"] = datetime.now(timezone.utc).isoformat()
            result["duration_seconds"] = perf_counter() - start
