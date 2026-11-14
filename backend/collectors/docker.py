
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
