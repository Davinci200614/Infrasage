
from backend.core.collection_orchestrator import CollectionOrchestrator


orchestrator = CollectionOrchestrator()

print("\n========== INITIAL COLLECTION ==========")

initial = orchestrator.collect_initial()

print("NETDATA:", initial["netdata"] is not None)
print("DOCKER:", initial["docker"] is not None)
print("ERRORS:", initial["errors"])


print("\n========== ON-DEMAND LOGS ==========")

logs = orchestrator.collect_on_demand(
    "logs",
    container_id="infrasage-test",
    tail=20
)

print("ERRORS:", logs["errors"])

if logs["data"] is not None:
    print("SUCCESS:", logs["data"]["success"])


print("\n========== ON-DEMAND EVENTS ==========")

events = orchestrator.collect_on_demand(
    "events",
    container_id="infrasage-test",
    lookback_seconds=60,
    event_actions=["start", "die", "restart", "oom"]
)

print("ERRORS:", events["errors"])

if events["data"] is not None:
    print("SUCCESS:", events["data"]["success"])
    print("EVENT COUNT:", events["data"]["event_count"])
