"""The fault harness must exercise the public launcher and pin its probe image."""
import json
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from scripts import run_voting_app_experiments as runner


def test_public_launcher_failure_is_not_reported_as_success(monkeypatch):
    monkeypatch.setattr(runner.subprocess, 'run', lambda *a, **k:
                        SimpleNamespace(returncode=1, stderr='Redis endpoint missing', stdout=''))
    with pytest.raises(RuntimeError, match='Public launcher failed: Redis endpoint missing'):
        runner.investigate_via_launcher('redis-id', 'worker-id', 'old-ip')


def test_public_launcher_output_preserves_investigation(monkeypatch):
    payload = {'model': {'version': 'test'}, 'investigation': {'result': {'decision': 'ABSTAIN'}}}
    execute = MagicMock(return_value=SimpleNamespace(
        returncode=0, stdout='Starting investigation\n' + json.dumps(payload), stderr=''))
    monkeypatch.setattr(runner.subprocess, 'run', execute)
    result = runner.investigate_via_launcher('redis-id', 'worker-id', 'old-ip')
    assert result['investigation'] == payload['investigation']
    assert execute.call_args.args[0] == [str(runner.ROOT / 'infrasage.sh'), 'investigate']
    assert execute.call_args.kwargs['timeout'] == 45


def test_helper_tag_is_resolved_before_fault_injection(tmp_path, monkeypatch):
    manifest = tmp_path / 'manifest.json'
    manifest.write_text('{}')
    client = MagicMock()
    client.images.get.return_value.id = 'sha256:' + 'c' * 64
    client.images.get.return_value.attrs = {'Config': {'Labels': {
        'org.infrasage.tcp-probe': 'v1',
        'org.infrasage.tcp-probe.sha256': runner.helper_source_sha()}}}
    monkeypatch.setattr(runner.docker, 'from_env', lambda **kw: client)
    lab = MagicMock()
    lab.run_scenario.return_value = {'artifact_path': 'trace.json',
                                    'restoration': {'success': True}, 'successful_experiment': True}
    create = MagicMock(return_value=lab)
    monkeypatch.setattr(runner, 'VotingAppLab', create)
    assert runner.main(['--manifest', str(manifest), '--helper-image', 'probe:tag']) == 0
    assert create.call_args.args[2] == 'sha256:' + 'c' * 64
    client.close.assert_called_once()


def test_outdated_helper_prevents_fault_injection(tmp_path, monkeypatch):
    client = MagicMock()
    client.images.get.return_value.attrs = {'Config': {'Labels': {}}}
    monkeypatch.setattr(runner.docker, 'from_env', lambda **kw: client)
    create = MagicMock()
    monkeypatch.setattr(runner, 'VotingAppLab', create)
    with pytest.raises(RuntimeError, match='outdated TCP probe'):
        runner.main(['--helper-image', 'probe:tag'])
    create.assert_not_called()
    client.close.assert_called_once()


def test_restoration_does_not_depend_on_probe_image(tmp_path, monkeypatch):
    manifest = tmp_path / 'manifest.json'
    manifest.write_text('{}')
    client = MagicMock()
    monkeypatch.setattr(runner.docker, 'from_env', lambda **kw: client)
    lab = MagicMock()
    lab.restore.return_value = {'success': True}
    monkeypatch.setattr(runner, 'VotingAppLab', lambda *a, **k: lab)
    assert runner.main(['--manifest', str(manifest), '--helper-image', 'missing',
                        '--restore-only', '--allow-fault-injection']) == 0
    client.images.get.assert_not_called()
    lab.restore.assert_called_once_with(lab.snapshot.return_value, restart_worker=True)
