"""Public investigation entry point for the twin (guide §10, open question §21.3).

The twin calls this and nothing else from the diagnostic system. Keep the shape
stable; the implementation behind it is free to change. This module must never
import ``backend.twin`` or the ground-truth store.

The target descriptor names the stack by Compose project and service labels. It
must not carry the fault name, label, or any hint about what was injected.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

Outcome = Literal["connected", "refused", "timeout", "error", "unknown"]
ExecutionStatus = Literal["completed", "error"]
EnergyMethod = Literal["measured", "estimated", "not_measured"]


@dataclass(frozen=True)
class LabTarget:
    compose_project: str = "infrasage-lab"
    services: tuple[str, ...] = ("vote", "redis", "worker", "db", "result")


@dataclass(frozen=True)
class TestResult:
    test_id: str
    outcome: Outcome
    execution_status: ExecutionStatus
    latency_ms: int
    observed_at: str | None = None
    collector_version: str | None = None
    energy_joules: float | None = None
    energy_method: EnergyMethod = "not_measured"
    raw_reference: str | None = None


@dataclass(frozen=True)
class InvestigationResult:
    decision: str  # cause label, or "abstain"
    abstained: bool
    posterior: dict[str, float] = field(default_factory=dict)
    tests: list[TestResult] = field(default_factory=list)
    total_tests: int = 0
    elapsed_seconds: float = 0.0


def investigate(target: LabTarget) -> InvestigationResult:
    """Run one investigation against ``target`` and freeze the result.

    The twin's experiment runner uses a local stub until the real engine is
    wired in (guide §18 step 5), so this raises rather than guessing at the
    diagnostic pipeline.
    """
    raise NotImplementedError("wire to backend.core / backend.diagnosis")
