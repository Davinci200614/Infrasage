"""Manual live collector check; importing this file does not run it."""
from pathlib import Path
import sys

if __package__ in (None, ''):
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))


def main():
    from backend.core.collection_orchestrator import CollectionOrchestrator

    orchestrator = CollectionOrchestrator()

    result = orchestrator.collect_on_demand(
        "logs",
        container_id="conteneur-inexistant"
    )

    print(result)


if __name__ == "__main__":
    main()
