
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

                # Additional information from Docker configuration
                config = attrs.get("Config", {})
                healthcheck_config = config.get("Healthcheck")

                # Docker Compose labels
                labels = config.get("Labels") or {}

                healthcheck_configured = bool(
                    healthcheck_config
                    and healthcheck_config.get("Test")
                    and healthcheck_config.get("Test") != ["NONE"]
                )

                # Docker network information
                network_settings = attrs.get(
                    "NetworkSettings", {}
                )
                networks = network_settings.get("Networks") or {}

                data = {
                    # Existing fields: preserved
                    "id": container.id,
                    "name": container.name,
                    "image": image_tags,
                    "status": state.get("Status"),
                    "health": memory_health.get("Status"),
                    "ports": network_settings.get("Ports") or {},
                    "restart_count": attrs.get("RestartCount", 0),
                    "cpu_percent": None,
                    "memory_usage_mb": None,
                    "memory_limit_mb": None,

                    # New fields
                    "running": state.get("Running"),
                    "paused": state.get("Paused"),
                    "restarting": state.get("Restarting"),
                    "dead": state.get("Dead"),
                    "exit_code": state.get("ExitCode"),
                    "state_error": state.get("Error"),
                    "started_at": state.get("StartedAt"),
                    "finished_at": state.get("FinishedAt"),

                    # Docker Compose information
                    "labels": labels,
                    "compose_service": labels.get(
                        "com.docker.compose.service"
                    ),
                    "compose_project": labels.get(
                        "com.docker.compose.project"
                    ),

                    # Healthcheck details
                    "healthcheck_configured": healthcheck_configured,
                    "health_failing_streak": memory_health.get(
                        "FailingStreak", 0
                    ),

                    # Network details
                    "network_names": list(networks.keys())
                }

                if data["status"] == "running":
                    stats = container.stats(stream=False)

                    data["cpu_percent"] = self.calculate_cpu_percent(
                        stats
                    )

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

                    data["memory_usage_mb"] = (
                        memory_used / (1024 ** 2)
                    )

                    data["memory_limit_mb"] = (
                        memory_stats["limit"] / (1024 ** 2)
                    )

                results.append(data)

            except docker.errors.APIError:
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
            return (
                (cpu_delta / system_delta)
                * number_cpus
                * 100
            )

        return 0.0
