from backend.core.collection_orchestrator import CollectionOrchestrator

orchestrator = CollectionOrchestrator()

result = orchestrator.collect_on_demand(
    "logs",
    container_id="conteneur-inexistant"
)

print(result)