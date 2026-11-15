"""Thin wrapper over the docker CLI. No SDK, no retries, no shell (guide §5)."""

import subprocess


def run(*args: str, timeout: float = 30):
    """Run ``docker <args>``; the caller decides what a non-zero status means."""
    return subprocess.run(["docker", *args], capture_output=True, text=True, timeout=timeout)
