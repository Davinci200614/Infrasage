"""Manual live collector check; importing this file does not run it."""
from pathlib import Path
import sys

if __package__ in (None, ''):
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))


def main():
    from backend.collectors.netdata import NetdataCollector

    collector = NetdataCollector()

    data = collector.collect()

    print("Source:")
    print(data["source"])

    print("\nCPU:")
    print(data["cpu"])

    print("\nRAM:")
    print(data["ram"])

    print("\nNetwork:")
    print(data["network"])

    print("\nErrors:")
    print(data["errors"])

    print("\nDerived metrics:")
    print(data.get("derived"))


if __name__ == "__main__":
    main()
