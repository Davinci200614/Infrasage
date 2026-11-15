"""Public diagnostic entry point shared with the offline twin.

Only target identities enter this API. Ground truth and fault injection remain
in the caller; diagnostic packages never import the twin.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import math
import time
from typing import Literal

import docker

from backend.collectors.tcp_probe import helper_source_sha

Outcome = str  # Native catalogue vocabulary: runtime state is not TCP status.
ExecutionStatus = Literal['completed', 'error']
EnergyMethod = Literal['measured', 'estimated', 'not_measured']


@dataclass(frozen=True)
class LabTarget:
    compose_project: str = 'infrasage-lab'
    services: tuple[str, ...] = ('vote', 'redis', 'worker', 'db', 'result')


@dataclass(frozen=True)
class TestResult:
    test_id: str
    outcome: Outcome
    execution_status: ExecutionStatus
    latency_ms: int
    observed_at: str | None = None
    collector_version: str | None = None
    energy_joules: float | None = None
    energy_method: EnergyMethod = 'not_measured'
    raw_reference: str | None = None
    target_identifier: str | None = None


@dataclass(frozen=True)
class InvestigationResult:
    decision: str
    abstained: bool
    posterior: dict[str, float] = field(default_factory=dict)
    tests: list[TestResult] = field(default_factory=list)
    total_tests: int = 0
    elapsed_seconds: float = 0.0


def _resolve_container(client, project, service):
    matches = client.containers.list(all=True, filters={'label': [
        f'com.docker.compose.project={project}', f'com.docker.compose.service={service}']})
    if len(matches) != 1:
        raise ValueError(f'Expected one {service} container in project {project}; found {len(matches)}')
    container = matches[0]
    container.reload()
    labels = container.attrs.get('Config', {}).get('Labels') or {}
    if (labels.get('com.docker.compose.project') != project
            or labels.get('com.docker.compose.service') != service):
        raise ValueError('Container labels changed during resolution')
    return container


def _probe_config(client, redis):
    # No endpoint means runtime-only. Missing/outdated helpers must not prevent
    # reading container state. No image pulls occur during an investigation.
    networks = redis.attrs.get('NetworkSettings', {}).get('Networks') or {}
    if not any(n.get('IPAddress') for n in networks.values()):
        return None
    try:
        helper = client.images.get('infrasage-tcp-probe:phase2b1')
    except docker.errors.ImageNotFound:
        return None
    labels = helper.attrs.get('Config', {}).get('Labels') or {}
    if (labels.get('org.infrasage.tcp-probe') != 'v1'
            or labels.get('org.infrasage.tcp-probe.sha256') != helper_source_sha()):
        return None
    # Resolve the same service name the worker uses, in its network namespace.
    return dict(redis_host='redis', redis_port=6379, connection_timeout=1.0,
                execution_deadline=8.0, helper_image=helper.id)


def _measure_action(energy_client, test_func):
    """Telemetry is optional: never lose or repeat an executed diagnostic."""
    start_failed = False
    try:
        energy_client.start_measurement()
    except Exception:
        start_failed = True
    reading = None
    try:
        raw = test_func()
    finally:
        try:
            reading = energy_client.stop_measurement()
        except Exception:
            pass
    if reading is not None and not start_failed:
        value, method = reading.energy_joules, reading.energy_method
        if (method in ('measured', 'estimated') and isinstance(value, (int, float))
                and not isinstance(value, bool) and math.isfinite(value) and value >= 0):
            return raw, value, method
    return raw, None, 'not_measured'


def investigate(target: LabTarget, energy_client=None) -> InvestigationResult:
    """Investigate one project through the existing verified evidence pipeline."""
    from backend.core.orchestrator import run_live_investigation, LiveInvestigationConfig, StoppingConfig
    from backend.core.collector_manager import CollectorManager
    from backend.diagnosis.catalogue_loader import load_catalogues
    from backend.optimization.planner import DiagnosticPolicy

    started = time.monotonic()
    catalogues = load_catalogues(research_mode=True)
    client = None
    manager = None
    try:
        client = docker.from_env(timeout=3)
        redis = _resolve_container(client, target.compose_project, 'redis')
        worker = _resolve_container(client, target.compose_project, 'worker')
        config = LiveInvestigationConfig(
            redis_target={'container_id': redis.id}, worker_target={'container_id': worker.id},
            probe_config=_probe_config(client, redis), include_worker_runtime=True)
        manager = CollectorManager(catalogue=catalogues)
        callback = (lambda tid, action: _measure_action(energy_client, action)) if energy_client else None
        live = run_live_investigation(catalogues, manager, config, DiagnosticPolicy('ratio'),
                                      StoppingConfig(max_tests=3), energy_measure=callback)
        tests = [TestResult(
            test_id=obs.test_identifier, outcome=_map_outcome(obs.observed_outcome),
            execution_status=_map_execution_status(obs.execution_status),
            latency_ms=int(obs.duration_seconds * 1000), observed_at=obs.timestamp.isoformat(),
            collector_version='docker-collector-v1', energy_joules=obs.measured_energy_joules,
            energy_method=obs.energy_method, target_identifier=obs.target_identifier,
        ) for obs in live.result.observations]
        decision = live.result.diagnosed_hypothesis or 'abstain'
        return InvestigationResult(decision, decision == 'abstain', dict(live.result.final_beliefs),
                                   tests, len(tests), time.monotonic() - started)
    finally:
        if manager is not None:
            manager.close()
        if client is not None:
            client.close()


def _map_outcome(outcome: str | None) -> str:
    return outcome if outcome is not None else 'unknown'


def _map_execution_status(status) -> str:
    return 'completed' if str(status).lower() == 'completed' else 'error'
