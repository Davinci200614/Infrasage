"""Deterministic vote workload (guide §6). Must run as its own process."""

from __future__ import annotations

import argparse
import json
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone

from backend.twin import catalogues

VOTES = ("a", "b")


def voter_id(seed: int, index: int) -> str:
    return f"w{seed}-{index}"


def submit(vote: str, voter: str, target: str, timeout: float = 10) -> None:
    """POST one vote. The voter_id cookie is what later proves it reached the DB."""
    request = urllib.request.Request(
        target.rstrip("/") + "/",
        data=urllib.parse.urlencode({"vote": vote}).encode(),
        method="POST",
        headers={
            "Content-Type": "application/x-www-form-urlencoded",
            "Cookie": f"voter_id={voter}",
        },
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        response.read()


def run(seconds: float, *, target: str, rate: float, seed: int) -> list[dict]:
    """Submit at ``rate`` for ``seconds``.

    A failed submission is recorded and swallowed: during an injected fault the
    frontend is expected to error, and the workload must keep running.
    """
    records: list[dict] = []
    interval = 1.0 / rate
    start = time.monotonic()
    index = 0
    while (time.monotonic() - start) < seconds:
        voter = voter_id(seed, index)
        vote = VOTES[index % len(VOTES)]
        record = {
            "index": index,
            "voter_id": voter,
            "vote": vote,
            "submitted_at": datetime.now(timezone.utc).isoformat(),
            "ok": True,
            "error": None,
        }
        try:
            submit(vote, voter, target)
        except OSError as exc:  # URLError/HTTPError: the vote app 500s when redis is down
            record["ok"] = False
            record["error"] = str(exc)
        records.append(record)
        index += 1
        delay = start + index * interval - time.monotonic()
        if delay > 0:
            time.sleep(delay)
    return records


def main(argv: list[str] | None = None) -> int:
    workload = catalogues.load("workloads")["workload"]
    parser = argparse.ArgumentParser(description="Submit votes at a fixed rate.")
    parser.add_argument("--seconds", type=float, default=float(workload["warmup_seconds"]))
    parser.add_argument("--rate", type=float, default=workload["rate_per_second"])
    parser.add_argument("--seed", type=int, default=workload["seed"])
    parser.add_argument("--target", default=workload["target"])
    args = parser.parse_args(argv)
    for record in run(args.seconds, target=args.target, rate=args.rate, seed=args.seed):
        print(json.dumps(record), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
