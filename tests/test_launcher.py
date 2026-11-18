"""The friendly launcher must preserve target ownership and fault visibility."""
import json
from unittest.mock import MagicMock

import pytest
from scripts import launch_voting_app as launcher


@pytest.fixture
def environment(tmp_path, monkeypatch):
    lab_dir = tmp_path / 'lab'
    lab_dir.mkdir()
    (lab_dir / 'voting-app-manifest.json').write_text(json.dumps({}))
    monkeypatch.setattr(launcher, 'ROOT', tmp_path)
    client = MagicMock()
    monkeypatch.setattr(launcher.docker, 'from_env', lambda **kwargs: client)
    lab = MagicMock()
    monkeypatch.setattr(launcher, 'VotingAppLab', lambda *args: lab)
    lab.owned_network.return_value.id = 'network-id'
    redis = MagicMock()
    redis.id = 'redis-id'
    redis.attrs = {'NetworkSettings': {'Networks': {
        'actual-network': {'NetworkID': 'network-id', 'IPAddress': '172.20.0.2'}}}}
    worker = MagicMock()
    worker.id = 'worker-id'
    lab.owned_container.side_effect = lambda name: redis if name == 'redis' else worker
    client.images.get.return_value.id = 'sha256:helper'
    client.images.get.return_value.attrs = {'Config': {'Labels': {
        'org.infrasage.tcp-probe': 'v1',
        'org.infrasage.tcp-probe.sha256': launcher.helper_source_sha()}}}
    execute = MagicMock(return_value=0)
    monkeypatch.setattr(launcher, 'investigate', execute)
    return client, lab, execute


def test_investigate_preserves_fault_and_resolves_owned_targets(environment):
    client, lab, execute = environment
    assert launcher.main(['investigate']) == 0
    lab.validate_resources.assert_called_once()
    lab.verify_baseline.assert_not_called()
    assert execute.call_args.args[0] == [
        '--redis-id', 'redis-id', '--worker-id', 'worker-id',
        '--redis-host', '172.20.0.2', '--redis-port', '6379',
        '--helper-image', 'sha256:helper']
    client.close.assert_called_once()


def test_start_requires_successful_baseline(environment):
    client, lab, execute = environment
    lab.verify_baseline.side_effect = TimeoutError('unhealthy')
    assert launcher.main(['start']) == 1
    execute.assert_not_called()
    client.close.assert_called_once()


def test_ownership_failure_prevents_diagnostics(environment):
    client, lab, execute = environment
    lab.validate_resources.side_effect = ValueError('ownership mismatch')
    assert launcher.main(['investigate']) == 1
    execute.assert_not_called()
    client.images.get.assert_not_called()
    client.close.assert_called_once()


def test_stale_helper_requires_rebuild(environment):
    client, lab, execute = environment
    client.images.get.return_value.attrs = {'Config': {'Labels': {}}}
    assert launcher.main(['investigate']) == 1
    execute.assert_not_called()


def test_start_verifies_vote_before_investigation(environment):
    client, lab, execute = environment
    execute.side_effect = lambda args: 0 if lab.verify_baseline.call_count == 1 else 1
    assert launcher.main(['start']) == 0
    lab.verify_baseline.assert_called_once()
    execute.assert_called_once()
    client.close.assert_called_once()


def test_status_does_not_require_helper_or_run_investigation(environment):
    client, lab, execute = environment
    lab.manifest = {'services': {'redis': 'redis-id', 'worker': 'worker-id'}}
    assert launcher.main(['status']) == 0
    client.images.get.assert_not_called()
    lab.verify_baseline.assert_not_called()
    execute.assert_not_called()
    client.close.assert_called_once()


def test_docker_connection_failure_is_readable(environment, monkeypatch, capsys):
    def unavailable(**kwargs):
        raise launcher.docker.errors.DockerException('Docker unavailable')

    monkeypatch.setattr(launcher.docker, 'from_env', unavailable)
    assert launcher.main(['investigate']) == 1
    assert 'Launcher failed: Docker unavailable' in capsys.readouterr().err


@pytest.mark.parametrize('attachments', [{}, {'owned': {'NetworkID': 'network-id', 'IPAddress': ''}}])
def test_missing_redis_endpoint_still_runs_runtime_diagnostic(environment, attachments):
    client, lab, execute = environment
    lab.owned_container('redis').attrs['NetworkSettings']['Networks'] = attachments
    assert launcher.main(['investigate']) == 0
    assert execute.call_args.args[0] == [
        '--redis-id', 'redis-id', '--worker-id', 'worker-id', '--runtime-only']
    lab.verify_baseline.assert_not_called()
    client.close.assert_called_once()


def test_ambiguous_network_identity_is_rejected(environment):
    client, lab, execute = environment
    lab.owned_container('redis').attrs['NetworkSettings']['Networks'] = {
        name: {'NetworkID': 'network-id', 'IPAddress': ip}
        for name, ip in [('first', '172.20.0.2'), ('second', '172.20.0.3')]}
    assert launcher.main(['investigate']) == 1
    execute.assert_not_called()
