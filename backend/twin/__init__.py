"""Digital twin subsystem (guide). Shared names only; diagnostics must never import this."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
LAB = ROOT / "lab"
LAB_PROJECT = "infrasage-lab"
