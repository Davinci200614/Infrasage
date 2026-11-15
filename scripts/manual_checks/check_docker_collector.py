"""Manual live collector check; importing this file does not run it."""
from pathlib import Path
import sys

if __package__ in (None, ''):
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))


def main():
    from backend.collectors.docker import DockerCollector

    collector = DockerCollector()

    containers = collector.collect()

    for container in containers:
        print("\n------------------------------")
        print("Nom :", container["name"])
        print("CPU :", container["cpu_percent"], "%")
        print("RAM utilisée :", container["memory_usage_mb"], "MB")
        print("RAM limite :", container["memory_limit_mb"], "MB")
        print("Statut :", container["status"])


if __name__ == "__main__":
    main()
