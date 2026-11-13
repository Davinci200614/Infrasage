"""Write-once ground-truth label store (guide §9).

Only the twin's runner and scorer touch this. Diagnostic packages are forbidden
from importing ``backend.twin`` at all; ``tests/test_isolation.py`` enforces it.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path

from backend.twin import ROOT

DIR = ROOT / "experiments" / "ground_truth"


def path_for(run_id: str) -> Path:
    if not run_id or run_id.startswith(".") or "/" in run_id or "\\" in run_id:
        raise ValueError(f"invalid run_id: {run_id!r}")
    return DIR / f"{run_id}.json"


def write(
    run_id: str,
    *,
    label: str,
    fault_version: int,
    injected_at: str,
    parameters: dict | None = None,
) -> Path:
    """Create the label file once, owner-only (0600). Never overwrites.

    Guide §8.1: call this after injection is verified and before investigation.
    """
    DIR.mkdir(mode=0o700, parents=True, exist_ok=True)
    path = path_for(run_id)
    record = {
        "run_id": run_id,
        "ground_truth_label": label,
        "fault_version": fault_version,
        "injected_at": injected_at,
        "parameters": parameters or {},
        "written_at": datetime.now(timezone.utc).isoformat(),
    }
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w") as handle:
        json.dump(record, handle, indent=2)
        handle.write("\n")
    return path


def read(run_id: str) -> dict:
    """For scoring, after the diagnosis is frozen (guide §8.1 step 9)."""
    return json.loads(path_for(run_id).read_text())
