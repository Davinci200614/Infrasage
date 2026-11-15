"""Manual live collector check; importing this file does not run it."""
from pathlib import Path
import sys

if __package__ in (None, ''):
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))


def main():
    from backend.collectors.docker_logs import DockerLogsCollector


    collector = DockerLogsCollector()

    result = collector.collect(
        container_id="infrasage-test",
        tail=100
    )

    print("SOURCE:", result["source"])
    print("SUCCESS:", result["success"])

    if result["success"]:
        print("\nCONTAINER:")
        print(result["container"])

        print("\nSTDOUT:")
        print(result["logs"]["stdout"])

        print("\nSTDERR:")
        print(result["logs"]["stderr"])

        print("\nLINE COUNTS:")
        print(result["line_counts"])

    else:
        print("ERROR:", result["error"])


if __name__ == "__main__":
    main()
