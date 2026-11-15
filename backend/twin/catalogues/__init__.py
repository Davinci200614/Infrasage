"""Versioned twin catalogues (guide §7, §17.1)."""

from pathlib import Path

import yaml

DIR = Path(__file__).resolve().parent


def load(name: str) -> dict:
    """Parse ``catalogues/<name>.yaml``. No cache: catalogues are read once per run."""
    return yaml.safe_load((DIR / f"{name}.yaml").read_text())
