"""Split assignment by seed (guide §14, §18 step 10)."""

from __future__ import annotations

CALIBRATION = "calibration"
EVALUATION = "evaluation"


def assign(seed: int) -> str:
    """Deterministic split from the run seed."""
    return CALIBRATION if seed % 2 == 0 else EVALUATION
