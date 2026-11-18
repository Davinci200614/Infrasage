from copy import deepcopy
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import Mock

import docker
import pytest

from backend.collectors.docker import DockerCollector
from backend.core.collector_manager import CollectorManager
from backend.diagnosis.catalogue_loader import load_catalogues

CID = 'a' * 64
OTHER = 'b' * 64


@pytest.fixture(scope='module')
def loaded():
    return load_catalogues(research_mode=True)


@pytest.fixture
def provider():
    obj = object.__new__(DockerCollector)
    container = SimpleNamespace(attrs={'Id':CID,'Name':'/redis-test','State':{'Status':'running','Running':True},'Config':{'Image':'redis:7-alpine'}})
    obj.client = SimpleNamespace(api=SimpleNamespace(base_url='http+docker://localhost'),containers=Mock())
    obj.client.containers.get.return_value = container
    return obj


@pytest.fixture
def manager(loaded,provider):
    return CollectorManager(loaded,docker_collector=provider)


def enabled(manager):
    r=manager.verify_runtime_binding('redis_runtime_state',{'container_name':'redis-test'})
    assert r['status']=='completed',r
    return r


def test_loader_compatibility_and_runtime_routing(manager,provider,loaded):
    original=deepcopy(loaded.test_metadata)
    test=loaded.tests['redis_runtime_state']
    assert manager.execute_test(test,{'container_name':'redis-test'})['status']=='unavailable'
    provider.client.containers.get.assert_not_called()
    enabled(manager)
    r=manager.execute_test(test,{'container_name':'redis-test'})
    assert r['status']=='completed'
    assert r['data']['status']=='running'
    assert r['target_identifier']=='docker:'+CID
    assert r['observed_at'] and r['retrieved_at']
    assert r['duration_seconds']>=0
    assert loaded.test_metadata==original
    live_tests = {'worker_runtime_state', 'worker_redis_connectivity'}
    assert sum(d['availability']['live'] for d in loaded.test_metadata.values()) == len(live_tests)
    for tid in live_tests:
        assert loaded.test_metadata[tid]['availability']['live'] is True
    provider.client.containers.list.assert_not_called()
    assert provider.client.containers.get.call_count==2


def test_raw_catalogue_envelope_compatibility(loaded,provider):
    m=CollectorManager(loaded.metadata['tests'],docker_collector=provider)
    assert m.execute_test('redis_runtime_state',{'container_name':'redis-test'})['status']=='unavailable'
    provider.client.containers.get.assert_not_called()
    assert m.verify_runtime_binding('redis_runtime_state',{'container_name':'redis-test'})['status']=='unavailable'


@pytest.mark.parametrize('test', ['missing','worker_redis_connectivity','worker_error_signature','redis_restart_activity','host_cpu_pressure'])
def test_other_tests_stay_disabled(manager,provider,test):
    assert manager.execute_test(test,{'container_name':'redis-test'})['status']=='unavailable'
    assert manager.verify_runtime_binding(test,{'container_name':'redis-test'})['status']=='unavailable'
    provider.client.containers.get.assert_not_called()


@pytest.mark.parametrize('target',[None,{}, {'container_id':'abc'}, {'container_id':True}, {'container_name':'redis*'}, {'container_name':' redis-test'}, {'container_name':''}, {'container_id':CID,'container_name':'different'}])
def test_reject_missing_ambiguous_or_mismatched_target(manager,target):
    r=manager.verify_runtime_binding('redis_runtime_state',target)
    assert r['status']!='completed'


@pytest.mark.parametrize('mutation',[
    lambda d:d['availability'].update(live=False),
    lambda d:d['execution'].update(binding_status='unverified'),
    lambda d:d['execution'].update(live_binding=True),
    lambda d:d['execution']['live_binding'].update(container_id='abc'),
    lambda d:d['execution']['live_binding'].update(freshness_max_seconds=float('nan')),
    lambda d:d['execution']['live_binding'].update(verified_at='bad'),
    lambda d:d['execution']['live_binding'].update(verified_at=(datetime.now(timezone.utc)-timedelta(seconds=60)).isoformat()),
    lambda d:d['execution'].update(logical_operation='read_bounded_worker_logs'),
    lambda d:d.update(provider='netdata'),
])
def test_policy_and_malformed_binding_denied_without_io(manager,provider,mutation):
    enabled(manager)
    provider.client.containers.get.reset_mock()
    mutation(manager._verified_definitions['redis_runtime_state'])
    r=manager.execute_test('redis_runtime_state',{'container_name':'redis-test'})
    assert r['status']=='unavailable'
    provider.client.containers.get.assert_not_called()


@pytest.mark.parametrize('error',[docker.errors.NotFound('missing'),docker.errors.APIError('denied'),RuntimeError('provider unavailable')])
def test_provider_failure_is_not_fault_outcome(manager,provider,error):
    provider.client.containers.get.side_effect=error
    r=manager.verify_runtime_binding('redis_runtime_state',{'container_name':'redis-test'})
    assert r['status'] in ('failed','unavailable')
    assert 'redis_runtime_state' not in manager._verified_definitions
    assert r['error']


def test_replaced_container_rejected(manager,provider):
    enabled(manager)
    provider.client.containers.get.return_value.attrs['Id']=OTHER
    r=manager.execute_test('redis_runtime_state',{'container_name':'redis-test'})
    assert r['status']=='failed'
    assert 'identity changed' in r['error']


def test_name_prefix_not_accepted(manager):
    r=manager.verify_runtime_binding('redis_runtime_state',{'container_name':'redis'})
    assert r['status']=='unavailable'
    assert 'ambiguous' in r['error']


def test_batch_collection_reused(loaded):
    original={'netdata':{},'docker':[],'errors':{}}
    coord=Mock()
    coord.collect_initial.return_value=original
    assert CollectorManager(loaded,coord).collect_snapshot() is original
    coord.collect_initial.assert_called_once()


def test_constructor_and_disabled_gate_have_no_docker_side_effect(loaded,monkeypatch):
    def forbidden():raise AssertionError('Docker must remain lazy')
    monkeypatch.setattr('backend.core.collector_manager.DockerCollector',forbidden)
    manager=CollectorManager(loaded)
    assert manager.execute_test('redis_runtime_state',{'container_name':'redis-test'})['status']=='unavailable'


@pytest.mark.parametrize('attrs', [{}, {'Id':CID,'Name':'/redis-test','State':None}, {'Id':'abc','Name':'/redis-test','State':{'Status':'running'}}])
def test_malformed_docker_inspection(manager,provider,attrs):
    provider.client.containers.get.return_value.attrs=attrs
    assert manager.verify_runtime_binding('redis_runtime_state',{'container_name':'redis-test'})['status']=='failed'


@pytest.mark.parametrize('test',[None,{}, {'identifier':[]}, {'identifier':True}])
def test_malformed_test_identifier(manager,provider,test):
    assert manager.verify_runtime_binding(test,{'container_name':'redis-test'})['status']=='unavailable'
    assert manager.execute_test(test,{'container_name':'redis-test'})['status']=='unavailable'
    provider.client.containers.get.assert_not_called()


@pytest.mark.parametrize('offset',[-60,60])
def test_verification_refuses_stale_or_future_provider_sample(manager,provider,offset):
    raw=provider.inspect_runtime(container_name='redis-test')
    raw['observed_at']=(datetime.now(timezone.utc)+timedelta(seconds=offset)).isoformat()
    provider.inspect_runtime=lambda **kwargs:raw
    assert manager.verify_runtime_binding('redis_runtime_state',{'container_name':'redis-test'})['status']=='unavailable'
    assert not manager._verified_definitions


def test_changed_identity_error_preserves_provider_evidence(manager,provider):
    enabled(manager)
    provider.client.containers.get.return_value.attrs['Id']=OTHER
    r=manager.execute_test('redis_runtime_state',{'container_name':'redis-test'})
    assert r['status']=='failed'
    assert r['data']['id']==OTHER
    assert r['observed_at']
    assert r['binding']['container_id']==CID


@pytest.mark.parametrize('state,expected', [('running','running'),('exited','stopped')])
def test_cli_through_real_manager_adapter_with_mock_docker(loaded,provider,monkeypatch,capsys,state,expected):
    from scripts.demo_live_diagnostic import main
    provider.client.close=Mock()
    provider.client.containers.get.return_value.attrs['State']['Status']=state
    monkeypatch.setattr('backend.core.collector_manager.DockerCollector',lambda:provider)
    assert main(['--container-name','redis-test','--expected-outcome',expected])==0
    import json
    output=json.loads(capsys.readouterr().out)
    assert output['observation']['observed_outcome']==expected
    assert output['catalogue_live_availability_unchanged'] is False
    assert output['provenance']['identity_verified']
    provider.client.close.assert_called_once()
