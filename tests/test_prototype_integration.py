"""Regression coverage for the collector/twin integration boundary."""
from datetime import datetime, timezone
import importlib
import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest

api = importlib.import_module('backend.api.investigate')


@pytest.mark.parametrize('outcome', ['running', 'stopped', 'restarting', 'connected', 'refused', 'timeout'])
def test_api_preserves_diagnostic_outcome_semantics(outcome):
    assert api._map_outcome(outcome) == outcome


def calibration_run(*, outcome='stopped', execution_status='completed', split='calibration',
                    lab_status='valid', label='redis_process_stopped'):
    return {
        'split': split, 'lab_status': lab_status, 'ground_truth_label': label,
        'injection': {'status': 'verified'},
        'diagnosis': {'tests': [{'test_id': 'redis_runtime_state', 'outcome': outcome,
                                 'execution_status': execution_status}]},
    }


def test_calibration_smooths_over_all_catalogue_outcomes():
    from backend.twin.scoring.aggregate import compute_likelihood_tables

    result = compute_likelihood_tables([calibration_run()])
    row = result['redis_process_stopped']['redis_runtime_state']
    assert row == pytest.approx({'running': .25, 'stopped': .5, 'restarting': .25})
    assert sum(row.values()) == pytest.approx(1)


@pytest.mark.parametrize('changes', [
    {'execution_status': 'error'}, {'execution_status': 'failed'},
    {'split': 'evaluation'}, {'lab_status': 'dirty'}, {'label': 'other_unknown'},
    {'outcome': 'unrecognized-outcome'},
])
def test_calibration_excludes_non_evidence_and_held_out_runs(changes):
    from backend.twin.scoring.aggregate import compute_likelihood_tables

    baseline = calibration_run()
    expected = compute_likelihood_tables([baseline])
    assert compute_likelihood_tables([baseline, calibration_run(**changes)]) == expected


def test_calibration_excludes_unverified_injection():
    from backend.twin.scoring.aggregate import compute_likelihood_tables

    run = calibration_run()
    run['injection']['status'] = 'failed'
    assert compute_likelihood_tables([run]) == {}


def test_service_resolution_rejects_ambiguous_compose_identity():
    client = MagicMock()
    client.containers.list.return_value = [MagicMock(), MagicMock()]
    with pytest.raises((ValueError, RuntimeError)):
        api._resolve_container(client, 'infrasage-lab', 'redis')
    assert client.containers.list.call_args.kwargs['all'] is True


@pytest.mark.parametrize('failure_phase', ['start_measurement', 'stop_measurement'])
def test_energy_telemetry_failure_does_not_discard_collected_evidence(failure_phase):
    raw = {'test_id': 'redis_runtime_state', 'status': 'completed', 'data': {'status': 'stopped'}}
    action = MagicMock(return_value=raw)
    energy = MagicMock()
    getattr(energy, failure_phase).side_effect = RuntimeError('measurement unavailable')
    evidence, joules, method = api._measure_action(energy, action)
    assert evidence is raw
    assert joules is None
    assert method == 'not_measured'
    action.assert_called_once_with()


@pytest.mark.parametrize('baseline_ok', [True, False])
def test_generated_twin_success_and_failure_records_validate(tmp_path, monkeypatch, baseline_ok):
    import jsonschema
    from backend.twin.ground_truth import store
    from backend.twin.runner import experiment

    monkeypatch.setattr(experiment, 'RUNS_DIR', tmp_path / 'runs')
    monkeypatch.setattr(store, 'DIR', tmp_path / 'ground_truth')
    monkeypatch.setattr(experiment.lab, 'reset', lambda: None)
    monkeypatch.setattr(experiment.lab, 'down', lambda: None)
    monkeypatch.setattr(experiment.lab, 'residue', lambda: {'containers': [], 'networks': [], 'volumes': []})
    monkeypatch.setattr(experiment.lab, 'check_baseline', lambda **kwargs: {'ok': baseline_ok})
    monkeypatch.setattr(experiment, 'run_workload', lambda **kwargs: [])
    monkeypatch.setattr(experiment.injector, 'apply', lambda fault: {
        'method': 'docker_stop', 'status': 'verified',
        'injected_at': datetime.now(timezone.utc).isoformat(),
    })
    monkeypatch.setattr(experiment.injector, 'revert', lambda fault: {'status': 'verified'})
    record = experiment.run('redis_process_stopped', seed=420,
                            run_id='redis_process_stopped-0001', warmup_seconds=0,
                            investigate=experiment.stub_investigate)
    assert record['lab_status'] == ('valid' if baseline_ok else 'invalid_baseline')
    schema_path = Path(__file__).resolve().parents[1] / 'experiments/schema/run_record.schema.json'
    jsonschema.validate(record, json.loads(schema_path.read_text()),
                        format_checker=jsonschema.FormatChecker())


@pytest.fixture
def bridge_environment(monkeypatch):
    from types import SimpleNamespace
    from backend.core import orchestrator
    from backend.core.models import Decision
    from backend.core import collector_manager

    client = MagicMock()
    project = 'infrasage-lab'
    containers = {}
    for service in ('redis', 'worker'):
        container = MagicMock()
        container.id = service[0] * 64
        container.attrs = {
            'Config': {'Labels': {'com.docker.compose.project': project,
                                  'com.docker.compose.service': service}},
            'NetworkSettings': {'Networks': {'infrasage-lab_back-tier': {'IPAddress': '172.18.0.2'}}},
        }
        containers[service] = container

    def list_containers(*, all, filters):
        assert all is True
        service_label = next(value for value in filters['label'] if value.startswith('com.docker.compose.service='))
        return [containers[service_label.split('=', 1)[1]]]

    client.containers.list.side_effect = list_containers
    client.images.get.return_value.id = 'sha256:' + 'f' * 64
    client.images.get.return_value.attrs = {'Config': {'Labels': {
        'org.infrasage.tcp-probe': 'v1', 'org.infrasage.tcp-probe.sha256': api.helper_source_sha(),
    }}}
    monkeypatch.setattr(api.docker, 'from_env', lambda **kwargs: client)
    manager = MagicMock()
    monkeypatch.setattr(collector_manager, 'CollectorManager', lambda **kwargs: manager)
    live = MagicMock(return_value=SimpleNamespace(result=SimpleNamespace(
        decision=Decision.ABSTAIN, diagnosed_hypothesis=None, final_beliefs={'other_unknown': 1.0}, observations=[])))
    monkeypatch.setattr(orchestrator, 'run_live_investigation', live)
    return client, manager, containers, live


def test_twin_bridge_resolves_local_helper_and_enables_worker_runtime(bridge_environment):
    client, manager, containers, live = bridge_environment
    result = api.investigate(api.LabTarget())
    config = live.call_args.args[2]
    stopping = live.call_args.args[4]
    assert config.probe_config['helper_image'] == 'sha256:' + 'f' * 64
    assert config.include_worker_runtime is True
    assert stopping.max_tests == 3
    assert config.redis_target == {'container_id': containers['redis'].id}
    assert config.worker_target == {'container_id': containers['worker'].id}
    assert result.abstained is True
    client.close.assert_called_once_with()
    manager.close.assert_called_once_with()


@pytest.mark.parametrize('reason', ['missing_helper', 'outdated_helper', 'no_redis_endpoint'])
def test_twin_bridge_keeps_runtime_checks_without_tcp_prerequisites(bridge_environment, reason):
    client, manager, containers, live = bridge_environment
    if reason == 'missing_helper':
        client.images.get.side_effect = api.docker.errors.ImageNotFound('missing')
    elif reason == 'outdated_helper':
        client.images.get.return_value.attrs['Config']['Labels']['org.infrasage.tcp-probe.sha256'] = 'outdated'
    else:
        containers['redis'].attrs['NetworkSettings']['Networks'] = {}
    api.investigate(api.LabTarget())
    config = live.call_args.args[2]
    assert config.probe_config is None
    assert config.include_worker_runtime is True
    assert config.redis_target['container_id'] == containers['redis'].id
    assert config.worker_target['container_id'] == containers['worker'].id
    client.close.assert_called_once_with()
    manager.close.assert_called_once_with()


def test_worker_runtime_uses_shared_verified_pipeline_without_tcp_configuration():
    from backend.core.collector_manager import CollectorManager
    from backend.core.models import ExecutionStatus
    from backend.core.orchestrator import LiveInvestigationConfig, StoppingConfig, run_live_investigation
    from backend.diagnosis.catalogue_loader import load_catalogues
    from backend.optimization.planner import DiagnosticPolicy

    loaded = load_catalogues(research_mode=True)
    manager = CollectorManager(loaded)
    cid, stamp = 'b' * 64, datetime.now(timezone.utc).isoformat()
    tid = 'worker_runtime_state'
    target = {'container_id': cid}
    raw = {
        'test_id': tid, 'source': 'docker', 'status': 'completed',
        'target': {'container_id': cid, 'container_name': 'worker'},
        'target_identifier': 'docker:' + cid,
        'data': {'id': cid, 'name': 'worker', 'status': 'exited'},
        'observed_at': stamp, 'retrieved_at': stamp, 'duration_seconds': .01,
        'docker_endpoint': 'http+docker://localhost',
        'binding': {'provider': 'docker', 'operation': 'container_process_state',
                    'container_id': cid, 'container_name': 'worker',
                    'docker_endpoint': 'http+docker://localhost',
                    'verified_at': stamp, 'freshness_max_seconds': 15},
        'diagnostic_actions': 1, 'action_phase': 'binding_verification_with_evidence',
    }
    manager.execute_investigation_test = MagicMock(return_value=raw)
    result = run_live_investigation(
        loaded, manager, LiveInvestigationConfig(worker_target=target, include_worker_runtime=True),
        DiagnosticPolicy('fixed', (tid,)), StoppingConfig(max_tests=1))
    manager.execute_investigation_test.assert_called_once()
    assert manager.execute_investigation_test.call_args.kwargs['deadline_seconds'] == 3
    assert len(result.evidence_records) == 1
    assert result.result.observations[0].execution_status == ExecutionStatus.COMPLETED
    assert result.result.observations[0].observed_outcome == 'stopped'
    assert result.result.final_beliefs != loaded.initial_prior


def test_abstention_does_not_discard_valid_calibration_observations():
    from backend.twin.scoring.aggregate import compute_likelihood_tables
    run = calibration_run()
    run['diagnosis']['abstained'] = True
    assert compute_likelihood_tables([run]) == compute_likelihood_tables([calibration_run()])


def test_validation_runs_are_excluded_from_loading(tmp_path, monkeypatch):
    from backend.twin.scoring import aggregate
    monkeypatch.setattr(aggregate, 'RUNS_DIR', tmp_path)
    for rid in ('validation-repro-001', 'redis_process_stopped-0001'):
        (tmp_path / (rid + '.json')).write_text(json.dumps({'run_id': rid}))
    assert [run['run_id'] for run in aggregate.load_runs()] == ['redis_process_stopped-0001']
