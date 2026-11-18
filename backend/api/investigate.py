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


# API contract types (twin imports these from backend.api.investigate)
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


def investigate(target: LabTarget, energy_client=None) -> InvestigationResult:
    """Run one investigation against ``target`` and freeze the result.

    Wires the diagnostic backend (catalogues, collectors, Bayesian engine, planner)
    into the twin's expected API contract.

    If ``energy_client`` is provided (from the twin), per-test energy is measured.
    """
    import time

    from backend.core.orchestrator import run_live_investigation, LiveInvestigationConfig, StoppingConfig
    from backend.core.collector_manager import CollectorManager
    from backend.core.collection_orchestrator import CollectionOrchestrator
    from backend.diagnosis.catalogue_loader import load_catalogues
    from backend.optimization.planner import DiagnosticPolicy

    # Load catalogues in research mode (required by Phase 1 loader)
    catalogues = load_catalogues(research_mode=True)

    # Create collector manager with collection orchestrator
    collection_orchestrator = CollectionOrchestrator()
    manager = CollectorManager(catalogue=catalogues, collection_orchestrator=collection_orchestrator)

    # Resolve container IDs from Compose project
    # The lab uses project name "infrasage-lab" with services: redis, worker
    redis_container_id = _resolve_container_id(target.compose_project, "redis")
    worker_container_id = _resolve_container_id(target.compose_project, "worker")

    if not redis_container_id or not worker_container_id:
        return InvestigationResult(
            decision="abstain",
            abstained=True,
            posterior={},
            tests=[],
            total_tests=0,
            elapsed_seconds=0.0,
        )

    # Build live investigation config
    config = LiveInvestigationConfig(
        redis_target={"container_id": redis_container_id},
        worker_target={"container_id": worker_container_id},
        probe_config={
            "redis_host": "redis",
            "redis_port": 6379,
            "connection_timeout": 1.0,
            "execution_deadline": 8.0,
            "helper_image": "sha256:a8a1f17f0e9f472225879441e0b0d265ca3cd3f14fdb5642909a3deb006667a9",
        },
        maximum_duration_seconds=30.0,
        runtime_deadline_seconds=10.0,
    )

    # Energy measurement callback
    def energy_measure(tid, test_func):
        energy_client.start_measurement()
        raw = test_func()
        reading = energy_client.stop_measurement()
        return raw, reading.energy_joules, reading.energy_method

    # Run the live investigation
    start = time.monotonic()
    live_result = run_live_investigation(
        catalogues=catalogues,
        manager=manager,
        configuration=config,
        policy=DiagnosticPolicy("ratio"),
        stopping=StoppingConfig(),
        energy_measure=energy_measure,
    )
    elapsed = time.monotonic() - start

    # Convert to API contract
    tests = []
    for obs in live_result.result.observations:
        outcome = _map_outcome(obs.observed_outcome)
        exec_status = _map_execution_status(obs.execution_status)
        tests.append(TestResult(
            test_id=obs.test_identifier,
            outcome=outcome,
            execution_status=exec_status,
            latency_ms=int(obs.duration_seconds * 1000),
            observed_at=obs.timestamp.isoformat() if obs.timestamp else None,
            collector_version="docker-collector-v1",
            energy_joules=obs.measured_energy_joules,
            energy_method=obs.energy_method,
            raw_reference=obs.target_identifier,
        ))

    # Map decision
    decision = live_result.result.decision.value.lower() if hasattr(live_result.result.decision, 'value') else str(live_result.result.decision).lower()
    if decision == "diagnosed":
        decision = live_result.result.diagnosed_hypothesis or "abstain"
    elif decision == "abstain":
        decision = "abstain"

    return InvestigationResult(
        decision=decision,
        abstained=(decision == "abstain"),
        posterior=dict(live_result.result.final_beliefs),
        tests=tests,
        total_tests=len(tests),
        elapsed_seconds=elapsed,
    )


def _resolve_container_id(compose_project: str, service: str) -> str | None:
    """Resolve a Compose service to its full 64-character container ID (running or stopped)."""
    import subprocess
    try:
        # Get all containers (including stopped) for this service
        result = subprocess.run(
            ["docker", "ps", "-a", "--filter", f"label=com.docker.compose.project={compose_project}",
             "--filter", f"label=com.docker.compose.service={service}",
             "--format", "{{.ID}}"],
            capture_output=True, text=True, timeout=5
        )
        if result.returncode != 0 or not result.stdout.strip():
            return None
        short_id = result.stdout.strip().splitlines()[0]
        # Get full ID via inspect
        result = subprocess.run(
            ["docker", "inspect", "-f", "{{.Id}}", short_id],
            capture_output=True, text=True, timeout=5
        )
        if result.returncode == 0 and result.stdout.strip():
            return result.stdout.strip()
    except Exception:
        pass
    return None


def _map_outcome(outcome: str | None) -> str:
    """Map internal outcome to API outcome vocabulary."""
    if outcome is None:
        return "unknown"
    mapping = {
        "running": "connected",
        "stopped": "refused",
        "restarting": "timeout",
        "connected": "connected",
        "refused": "refused",
        "timeout": "timeout",
        "healthy": "connected",
        "unreachable": "refused",
        "degraded": "timeout",
        "none": "connected",
        "recent": "timeout",
        "repeated": "timeout",
        "correct": "connected",
        "incorrect": "refused",
        "normal": "connected",
        "high": "timeout",
        "critical": "refused",
        "exhausted": "refused",
        "consistent": "connected",
        "delayed": "timeout",
        "inconsistent": "refused",
        "redis_error": "error",
        "postgres_error": "error",
        "other_error": "error",
    }
    return mapping.get(outcome, "unknown")


def _map_execution_status(status) -> str:
    """Map internal ExecutionStatus to API ExecutionStatus."""
    mapping = {
        "completed": "completed",
        "failed": "error",
        "unavailable": "error",
    }
    return mapping.get(str(status).lower(), "error")