"""Docker-free test doubles, so the twin self-tests run without a daemon (guide §15)."""

from __future__ import annotations

import json
import re
import subprocess
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs

import pytest

from backend.twin import docker

SERVICES = ("vote", "result", "redis", "db", "worker")


def _filter_value(args: tuple[str, ...], key: str) -> str | None:
    for arg in args:
        if key in arg:
            return arg.rsplit("=", 1)[-1]
    return None


class FakeDocker:
    """Answers exactly the docker CLI calls that lab.py and injector.py make."""

    def __init__(self):
        self.calls: list[tuple[str, ...]] = []
        self.running = {name: True for name in SERVICES}
        self.votes: dict[str, str] = {}
        self.leftovers = {"containers": [], "volumes": [], "networks": []}
        self.stop_takes_effect = True
        self.ps_format = "ndjson"
        self.psql_sees_votes = True

    def run(self, *args: str, timeout: float = 30):
        self.calls.append(args)
        stdout, code = self._respond(args)
        return subprocess.CompletedProcess(["docker", *args], code, stdout, "")

    def _respond(self, args: tuple[str, ...]) -> tuple[str, int]:
        head = args[0]
        if head == "ps":
            service = _filter_value(args, "com.docker.compose.service=")
            if service:
                return (f"cid-{service}\n" if service in self.running else ""), 0
            return "".join(f"{item}\n" for item in self.leftovers["containers"]), 0
        if head in ("volume", "network") and "ls" in args:
            return "".join(f"{item}\n" for item in self.leftovers[f"{head}s"]), 0
        if head == "stop":
            service = args[1].removeprefix("cid-")
            if self.stop_takes_effect:
                self.running[service] = False
            return "", 0
        if head == "start":
            self.running[args[1].removeprefix("cid-")] = True
            return "", 0
        if head == "inspect":
            service = args[-1].removeprefix("cid-")
            return ("true" if self.running.get(service) else "false") + "\n", 0
        if head == "compose":
            return self._compose(args)
        return "", 0

    def _compose(self, args: tuple[str, ...]) -> tuple[str, int]:
        if "ps" in args:
            rows = [
                {
                    "Service": name,
                    "Name": f"infrasage-lab-{name}-1",
                    "State": "running" if self.running[name] else "exited",
                    "Health": "",
                }
                for name in SERVICES
            ]
            if self.ps_format == "array":
                return json.dumps(rows), 0
            return "".join(json.dumps(row) + "\n" for row in rows), 0
        if "up" in args:
            self.running = {name: True for name in SERVICES}
            return "", 0
        if "down" in args:
            self.running = {name: False for name in SERVICES}
            return "", 0
        if "exec" in args:
            start = args.index("exec")
            _flag, service, *command = args[start + 1 :]
            if command[:2] == ["redis-cli", "ping"]:
                return "PONG\n", 0
            if command[:2] == ["pg_isready", "-U"]:
                return "accepting connections\n", 0
            if command[0] == "psql":
                if not self.psql_sees_votes:
                    return "\n", 0
                match = re.search(r"id = '([^']*)'", command[-1])
                return self.votes.get(match.group(1), "") + "\n" if match else "\n", 0
        return "", 0


class _VoteHandler(BaseHTTPRequestHandler):
    """Accepts the vote POST and records voter_id -> vote like the real app."""

    def do_POST(self):  # noqa: N802 - http.server API
        body = self.rfile.read(int(self.headers["Content-Length"])).decode()
        vote = parse_qs(body)["vote"][0]
        voter = self.headers["Cookie"].split("voter_id=", 1)[1].split(";")[0]
        self.server.votes[voter] = vote
        self.send_response(200)
        self.send_header("Content-Length", "2")
        self.end_headers()
        self.wfile.write(b"ok")

    def log_message(self, *args):  # keep pytest output clean
        pass


@pytest.fixture
def lab_env(monkeypatch):
    """Fake docker CLI + a real local HTTP vote stub, wired to each other."""
    fake = FakeDocker()
    server = ThreadingHTTPServer(("127.0.0.1", 0), _VoteHandler)
    server.votes = fake.votes
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    monkeypatch.setattr(docker, "run", fake.run)
    fake.target = f"http://127.0.0.1:{server.server_port}"
    try:
        yield fake
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
