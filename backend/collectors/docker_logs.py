import docker
from datetime import datetime, timezone

from backend.collectors.base import BaseCollector


class DockerLogsCollector(BaseCollector):

    def __init__(self):
        self.client = docker.from_env()

    def collect(
        self,
        container_id,
        tail=100,
        since=None,
        until=None
    ):
        """
        Récupère les logs d'un conteneur Docker.

        Args:
            container_id (str): Nom ou ID du conteneur.
            tail (int): Nombre de lignes récentes à récupérer.
            since (int | None): Timestamp Unix de début.
            until (int | None): Timestamp Unix de fin.

        Returns:
            dict: Logs et métadonnées du conteneur.
        """

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
            # 1. Récupération du conteneur
            container = self.client.containers.get(container_id)
            container.reload()

            # 2. Métadonnées
            container_info = {
                "id": container.id,
                "name": container.name,
                "status": container.status,
                "image": container.image.tags
            }

            # 3. Récupération de stdout
            stdout = container.logs(
                stdout=True,
                stderr=False,
                tail=tail,
                since=since,
                until=until,
                timestamps=True,
                stream=False
            )

            # 4. Récupération de stderr
            stderr = container.logs(
                stdout=False,
                stderr=True,
                tail=tail,
                since=since,
                until=until,
                timestamps=True,
                stream=False
            )

            # 5. Décodage des logs
            stdout_text = stdout.decode(
                "utf-8",
                errors="replace"
            ) if stdout else ""

            stderr_text = stderr.decode(
                "utf-8",
                errors="replace"
            ) if stderr else ""

            return {
                "source": "docker_logs",
                "success": True,
                "container": container_info,
                "collected_at": datetime.now(
                    timezone.utc
                ).isoformat(),
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
                }
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