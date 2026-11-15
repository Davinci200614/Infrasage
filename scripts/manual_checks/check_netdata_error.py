"""Manual live collector check; importing this file does not run it."""
from pathlib import Path
import sys

if __package__ in (None, ''):
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))


def main():
    from backend.collectors.netdata import NetdataCollector

    collector = NetdataCollector("http://localhost:19998")

    data = collector.collect()

    print(data)


if __name__ == "__main__":
    main()
