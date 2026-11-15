"""Manual live collector check; importing this file does not run it."""
from pathlib import Path
import sys

if __package__ in (None, ''):
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))


def main():
    from backend.collectors.docker_events import DockerEventsCollector


    collector = DockerEventsCollector()

    result = collector.collect(
        container_id="infrasage-test",
        lookback_seconds=60,
        event_actions=[
            "start",
            "die",
            "oom",
            "restart",
            "stop",
            "kill"
        ]
    )

    print("SOURCE:", result["source"])
    print("SUCCESS:", result["success"])

    if result["success"]:

        print("\nEVENT COUNT:", result["event_count"])

        for event in result["events"]:
            print("\n------------------------------")
            print("ACTION:", event["action"])
            print("CONTAINER ID:", event["actor_id"])
            print("ATTRIBUTES:", event["attributes"])
            print("TIMESTAMP:", event["timestamp"])

    else:
        print("\nERROR:", result["error"])


if __name__ == "__main__":
    main()
