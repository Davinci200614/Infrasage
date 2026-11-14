
from backend.collectors.netdata import NetdataCollector
from backend.collectors.docker import DockerCollector
from backend.collectors.docker_logs import DockerLogsCollector
from backend.collectors.docker_events import DockerEventsCollector


class CollectionOrchestrator:

    # Registre des collecteurs complémentaires
    ON_DEMAND_COLLECTORS = {
        "logs": DockerLogsCollector,
        "events": DockerEventsCollector
    }

    def __init__(self):
        # Collecteurs principaux
        self.netdata_collector = NetdataCollector()
        self.docker_collector = DockerCollector()

    def collect_initial(self):
        """
        Collecte initiale des métriques système et Docker.
        Les deux collecteurs sont exécutés indépendamment.
        """

        results = {
            "netdata": None,
            "docker": None,
            "errors": {}
        }

        # Collecte Netdata
        try:
            results["netdata"] = self.netdata_collector.collect()

        except Exception as error:
            results["errors"]["netdata"] = str(error)

        # Collecte Docker
        try:
            results["docker"] = self.docker_collector.collect()

        except Exception as error:
            results["errors"]["docker"] = str(error)

        return results

    def collect_all(self):
        """
        Compatibilité avec l'ancienne interface.
        """

        return self.collect_initial()

    def collect_on_demand(self, collector_name, **parameters):
        """
        Exécute un collecteur complémentaire à la demande.

        collector_name:
            "logs" ou "events"

        parameters:
            Paramètres transmis au collecteur concerné.
        """

        collector_name = (
            collector_name.strip().lower()
            if isinstance(collector_name, str)
            else ""
        )

        results = {
            "collector": collector_name,
            "data": None,
            "errors": {}
        }

        collector_class = self.ON_DEMAND_COLLECTORS.get(
            collector_name
        )

        if collector_class is None:
            results["errors"][collector_name or "unknown"] = (
                f"Unknown collector: {collector_name}"
            )
            return results

        try:
            collector = collector_class()

            data = collector.collect(**parameters)
            results["data"] = data

            # Vérification des erreurs signalées par le collecteur
            if (
                isinstance(data, dict)
                and data.get("success") is False
            ):
                results["errors"][collector_name] = data.get(
                    "error",
                    "Collection failed"
                )

        except Exception as error:
            results["errors"][collector_name] = str(error)

        return results
