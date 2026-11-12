
import docker
from backend.collectors.base import BaseCollector


class DockerCollector(BaseCollector):

    def __init__(self):
        self.client = docker.from_env()

    def collect(self):
        containers = self.client.containers.list(all=True)

        results = []

        for container in containers:

            data = {
                "id": container.short_id,
                "name": container.name,
                "image": container.image.tags,
                "status": container.status,
                "ports": container.attrs["NetworkSettings"]["Ports"],
                "restart_count": container.attrs["RestartCount"],
                "cpu_percent": None,
                "memory_usage_mb": None,
                "memory_limit_mb": None,
                "logs": None
                
            }
            try:
                logs = container.logs(
                    tail=50,
                 timestamps=True
                 )

                data["logs"] = logs.decode("utf-8", errors="replace")

            except docker.errors.APIError:
                        data["logs"] = "Impossible de récupérer les logs"
    

            if container.status == "running":

                stats = container.stats(stream=False)

                data["cpu_percent"] = self.calculate_cpu_percent(stats)

                memory_stats = stats["memory_stats"]

                data["memory_usage_mb"] = (
                    memory_stats["usage"] / (1024 ** 2)
                )

                data["memory_limit_mb"] = (
                    memory_stats["limit"] / (1024 ** 2)
                )

            results.append(data)

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
