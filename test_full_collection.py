
import json

from backend.core.collection_orchestrator import CollectionOrchestrator


def display(title, data):
    print("\n" + "=" * 80)
    print(title)
    print("=" * 80)

    print(
        json.dumps(
            data,
            indent=4,
            ensure_ascii=False,
            default=str
        )
    )


def main():
    orchestrator = CollectionOrchestrator()

    # 1. Collecte initiale : Netdata + Docker
    print("\nCOLLECTE INITIALE EN COURS...")

    initial = orchestrator.collect_initial()

    display("DONNEES NETDATA", initial["netdata"])
    display("DONNEES DOCKER", initial["docker"])
    display("ERREURS COLLECTE INITIALE", initial["errors"])

    # 2. Collecte des logs Docker
    print("\nCOLLECTE DES LOGS EN COURS...")

    logs = orchestrator.collect_on_demand(
        "logs",
        container_id="infrasage-test",
        tail=20
    )

    display("DONNEES DOCKER LOGS", logs)

    # 3. Collecte des événements Docker
    print("\nCOLLECTE DES EVENEMENTS EN COURS...")

    events = orchestrator.collect_on_demand(
        "events",
        container_id="infrasage-test",
        lookback_seconds=60,
        event_actions=["start", "die", "restart", "oom"]
    )

    display("DONNEES DOCKER EVENTS", events)

    print("\n" + "=" * 80)
    print("FIN DU TEST GLOBAL")
    print("=" * 80)


if __name__ == "__main__":
    main()
