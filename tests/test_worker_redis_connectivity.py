from datetime import datetime, timedelta, timezone
import errno
import json
from types import SimpleNamespace
from unittest.mock import Mock

import docker
import pytest
import requests

from backend.collectors.docker import DockerCollector
from backend.collectors.tcp_probe import helper_source_sha, valid_probe_config
from backend.collectors import tcp_probe_helper
from backend.core.collector_manager import CollectorManager
from backend.core.diagnostic_adapter import DiagnosticAdapter
from backend.core.models import ExecutionStatus
from backend.diagnosis.catalogue_loader import load_catalogues

CID='a'*64
HID='b'*64
IID='sha256:'+'c'*64
TARGET={'container_name':'worker-test'}
CONFIG=dict(redis_host='redis',redis_port=6379,connection_timeout=.3,execution_deadline=5.,helper_image=IID)


@pytest.fixture(scope='module')
def loaded():return load_catalogues(research_mode=True)


@pytest.fixture
def provider():
    p=object.__new__(DockerCollector)
    worker=SimpleNamespace(id=CID,attrs={'Id':CID,'Name':'/worker-test','State':{'Status':'running'},'Config':{'Image':'lab-worker'}})
    helper=Mock()
    helper.id=HID
    helper.attrs={'HostConfig':{'NetworkMode':'container:'+CID},'Image':IID}
    helper.wait.return_value={'StatusCode':0}
    api=SimpleNamespace(base_url='http+docker://localhost',timeout=60)
    p.client=SimpleNamespace(api=api,containers=Mock(),images=Mock())
    p.client.containers.get.return_value=worker
    p.client.images.get.return_value=SimpleNamespace(id=IID,attrs={'Config':{'Labels':{
        'org.infrasage.tcp-probe':'v1','org.infrasage.tcp-probe.sha256':helper_source_sha()}}})
    p.client.containers.create.return_value=helper
    def output(**kwargs):
        request=json.loads(p.client.containers.create.call_args.kwargs['command'][0])
        return json.dumps({'protocol':'infrasage-tcp-v1','request_id':request['request_id'],
                           'worker_id':CID,'endpoint':{'host':'redis','port':6379},
                           'status':'completed','outcome':'connected',
                           'observed_at':datetime.now(timezone.utc).isoformat()}).encode()
    helper.logs.side_effect=output
    return p


@pytest.fixture
def manager(loaded,provider):return CollectorManager(loaded,docker_collector=provider)


def verify(manager,config=None,target=None):
    return manager.verify_connectivity_binding('worker_redis_connectivity',TARGET if target is None else target,**(CONFIG if config is None else config))


@pytest.mark.parametrize('outcome',['connected','refused','timeout'])
def test_socket_outcomes_through_manager_adapter(manager,provider,loaded,outcome):
    helper=provider.client.containers.create.return_value
    original=helper.logs.side_effect
    def output(**kwargs):
        payload=json.loads(original(**kwargs));payload['outcome']=outcome
        return json.dumps(payload).encode()
    helper.logs.side_effect=output
    verification=verify(manager)
    assert verification['status']=='completed',verification
    result=manager.execute_test(loaded.tests['worker_redis_connectivity'],TARGET)
    assert result['status']=='completed',result
    r=DiagnosticAdapter().adapt_with_metadata(loaded.tests['worker_redis_connectivity'],result,TARGET)
    assert r.observation.execution_status==ExecutionStatus.COMPLETED
    assert r.observation.observed_outcome==outcome
    assert r.observation.target_identifier=='docker:'+CID
    assert r.observation.timestamp.isoformat()==result['observed_at']
    assert r.observation.measured_energy_joules is None
    assert r.metadata['execution_result']==result
    assert provider.client.api.timeout==60
    assert helper.remove.call_count==2
    live_tests = {'worker_runtime_state', 'worker_redis_connectivity'}
    for tid, d in loaded.test_metadata.items():
        if tid in live_tests:
            assert d['availability']['live'] is True
        else:
            assert d['availability']['live'] is False
    creation=provider.client.containers.create.call_args
    assert creation.args==(IID,)
    assert creation.kwargs['network_mode']=='container:'+CID
    assert creation.kwargs['privileged'] is False
    assert 'ports' not in creation.kwargs and 'volumes' not in creation.kwargs
    assert creation.kwargs['user']=='65534:65534'
    assert creation.kwargs['read_only']
    assert creation.kwargs['cap_drop']==['ALL']
    provider.client.containers.run.assert_not_called()
    provider.client.images.pull.assert_not_called()


def test_disabled_until_verified(manager,provider):
    assert manager.execute_test('worker_redis_connectivity',TARGET)['status']=='unavailable'
    provider.client.containers.create.assert_not_called()


@pytest.mark.parametrize('field,value',[
    ('redis_host','redis;sh'),('redis_host',''),('redis_host','redis/a'),
    ('redis_port',0),('redis_port',65536),('redis_port',True),('redis_port',6379.),
    ('connection_timeout',float('nan')),('connection_timeout',0),('connection_timeout',4),
    ('execution_deadline',float('inf')),('execution_deadline',2),('execution_deadline',11),
    ('helper_image','python:latest'),('helper_image',None)])
def test_invalid_probe_config_rejected_without_io(manager,provider,field,value):
    config={**CONFIG,field:value}
    assert not valid_probe_config(config)
    assert verify(manager,config)['status']=='unavailable'
    provider.client.containers.get.assert_not_called()


@pytest.mark.parametrize('target',[{}, {'container_id':'abc'}, {'container_name':'worker*'}, {'container_id':True}])
def test_bad_worker_identity(manager,provider,target):
    assert verify(manager,target=target)['status']=='unavailable'
    provider.client.containers.get.assert_not_called()


@pytest.mark.parametrize('state',['exited','paused','restarting'])
def test_worker_unavailable(manager,provider,state):
    provider.client.containers.get.return_value.attrs['State']['Status']=state
    assert verify(manager)['status']=='unavailable'
    provider.client.containers.create.assert_not_called()


def test_wrong_worker_not_probed(manager,provider):
    provider.client.containers.get.return_value.attrs['Id']='d'*64
    assert verify(manager,target={'container_id':CID})['status']=='unavailable'
    provider.client.containers.create.assert_not_called()


@pytest.mark.parametrize('error',[docker.errors.NotFound('missing'),docker.errors.APIError('denied')])
def test_worker_inspection_provider_errors(manager,provider,error):
    provider.client.containers.get.side_effect=error
    assert verify(manager)['status'] in ('failed','unavailable')
    provider.client.containers.create.assert_not_called()


def test_helper_image_unavailable(manager,provider):
    provider.client.images.get.side_effect=docker.errors.NotFound('no local image')
    assert verify(manager)['status']=='unavailable'
    provider.client.images.pull.assert_not_called()


def test_unrecognized_helper_build(manager,provider):
    provider.client.images.get.return_value.attrs['Config']['Labels']['org.infrasage.tcp-probe.sha256']='wrong'
    assert verify(manager)['status']=='unavailable'
    provider.client.containers.create.assert_not_called()


@pytest.mark.parametrize('stage',['start','wait','logs','reload'])
def test_probe_failure_or_timeout_always_cleans_up(manager,provider,stage):
    helper=provider.client.containers.create.return_value
    getattr(helper,stage).side_effect=requests.ReadTimeout('execution deadline')
    r=verify(manager)
    assert r['status']=='failed'
    assert not manager._verified_definitions
    helper.remove.assert_called_once_with(force=True,v=True)
    assert provider.client.api.timeout==60


def test_create_failure_cleanup_by_unique_name(manager,provider):
    worker=provider.client.containers.get.return_value
    orphan=Mock(id=HID)
    def create(*args,**kwargs):
        request=json.loads(kwargs['command'][0])
        orphan.attrs={'Config':{'Labels':{'org.infrasage.tcp-probe.helper':request['request_id']}}}
        raise requests.ReadTimeout('create timed out')
    provider.client.containers.create.side_effect=create
    provider.client.containers.get.side_effect=lambda name:worker if name=='worker-test' else orphan
    r=verify(manager)
    assert r['status']=='failed'
    orphan.remove.assert_called_once_with(force=True,v=True)
    assert provider.client.api.timeout==60


def test_cleanup_failure_is_execution_failure(manager,provider):
    helper=provider.client.containers.create.return_value
    helper.remove.side_effect=docker.errors.APIError('daemon unavailable')
    r=verify(manager)
    assert r['status']=='failed' and r['cleanup_error']
    assert r['helper_id']==HID
    assert not manager._verified_definitions


@pytest.mark.parametrize('field,value',[
    ('worker_id','d'*64),('endpoint',{'host':'other','port':6379}),('request_id','other'),
    ('protocol','other'),('outcome','dns_failure'),('observed_at',None),('status','failed')])
def test_malformed_or_mismatched_helper_results(manager,provider,field,value):
    helper=provider.client.containers.create.return_value
    original=helper.logs.side_effect
    def output(**kwargs):
        payload=json.loads(original(**kwargs));payload[field]=value
        return json.dumps(payload).encode()
    helper.logs.side_effect=output
    assert verify(manager)['status']=='failed'
    helper.remove.assert_called_once()


@pytest.mark.parametrize('payload',[b'',b'not-json',b'[]',b'x'*5000])
def test_invalid_helper_output(manager,provider,payload):
    helper=provider.client.containers.create.return_value
    helper.logs.side_effect=None;helper.logs.return_value=payload
    assert verify(manager)['status']=='failed'
    helper.remove.assert_called_once()


def test_namespace_mismatch_prevents_start(manager,provider):
    helper=provider.client.containers.create.return_value
    helper.attrs['HostConfig']['NetworkMode']='host'
    assert verify(manager)['status']=='failed'
    helper.start.assert_not_called();helper.remove.assert_called_once()


def test_expired_binding_and_different_endpoint_no_io(manager,provider):
    assert verify(manager)['status']=='completed'
    helper=provider.client.containers.create;helper.reset_mock()
    assert manager.execute_test('worker_redis_connectivity',{**TARGET,'redis_host':'other'})['status']=='unavailable'
    binding=manager._verified_definitions['worker_redis_connectivity']['execution']['live_binding']
    binding['verified_at']=(datetime.now(timezone.utc)-timedelta(seconds=16)).isoformat()
    assert manager.execute_test('worker_redis_connectivity',TARGET)['status']=='unavailable'
    helper.assert_not_called()


@pytest.mark.parametrize('mutation',[
    lambda r:r.update(test_id='redis_runtime_state'),
    lambda r:r['data'].update(id='d'*64),
    lambda r:r.update(namespace='host'),
    lambda r:r.update(helper_removed=False),
    lambda r:r['data']['endpoint'].update(host='other'),
    lambda r:r.pop('observed_at'),
    lambda r:r['probe_config'].update(redis_port=6380),
])
def test_adapter_strict_identity_endpoint_and_timestamps(manager,loaded,mutation):
    assert verify(manager)['status']=='completed'
    r=manager.execute_test('worker_redis_connectivity',TARGET)
    mutation(r)
    result=DiagnosticAdapter().adapt_with_metadata(loaded.tests['worker_redis_connectivity'],r,TARGET)
    assert result.observation.execution_status!=ExecutionStatus.COMPLETED
    assert result.observation.observed_outcome is None


def test_fixed_helper_socket_logic(monkeypatch):
    config={**CONFIG,'request_id':'test','worker_id':CID}
    monkeypatch.setattr(tcp_probe_helper.socket,'getaddrinfo',lambda *a,**kw:[(2,1,6,'',('172.20.0.3',6379))])
    sock=Mock();sock.__enter__=Mock(return_value=sock);sock.__exit__=Mock(return_value=False)
    monkeypatch.setattr(tcp_probe_helper.socket,'socket',lambda *a:sock)
    assert tcp_probe_helper.run_probe(config)['outcome']=='connected'
    sock.connect.side_effect=ConnectionRefusedError(errno.ECONNREFUSED,'refused')
    assert tcp_probe_helper.run_probe(config)['outcome']=='refused'
    sock.connect.side_effect=TimeoutError('socket deadline')
    assert tcp_probe_helper.run_probe(config)['outcome']=='timeout'
    sock.connect.side_effect=OSError(errno.ENETUNREACH,'route unavailable')
    r=tcp_probe_helper.run_probe(config)
    assert r['status']=='failed' and 'outcome' not in r


def test_dns_and_execution_deadline_are_not_socket_outcomes(monkeypatch):
    config={**CONFIG,'request_id':'test','worker_id':CID}
    def dns_error(*args,**kwargs):raise tcp_probe_helper.socket.gaierror('NXDOMAIN')
    monkeypatch.setattr(tcp_probe_helper.socket,'getaddrinfo',dns_error)
    r=tcp_probe_helper.run_probe(config)
    assert r['status']=='failed' and 'DNS' in r['error'] and 'outcome' not in r
    def deadline(*args,**kwargs):raise tcp_probe_helper.ProbeDeadline()
    monkeypatch.setattr(tcp_probe_helper.socket,'getaddrinfo',deadline)
    r=tcp_probe_helper.run_probe(config)
    assert r['status']=='failed' and 'outcome' not in r


def test_total_active_deadline_reserves_cleanup(manager,provider,monkeypatch):
    clock=[0.]
    monkeypatch.setattr('backend.collectors.tcp_probe.time.monotonic',lambda:clock[0])
    helper=provider.client.containers.create.return_value
    def waited(**kwargs):
        clock[0]=4.  # 5 s total - 2 s cleanup reserve: active deadline was 3 s.
        return {'StatusCode':0}
    helper.wait.side_effect=waited
    r=verify(manager)
    assert r['status']=='failed'
    assert 'deadline' in r['error']
    helper.logs.assert_not_called()
    helper.remove.assert_called_once()
    assert r['duration_seconds']==4.
    assert provider.client.api.timeout==60


def test_cleanup_failure_preserves_initial_execution_error(manager,provider):
    helper=provider.client.containers.create.return_value
    helper.start.side_effect=RuntimeError('launch denied')
    helper.remove.side_effect=RuntimeError('cleanup unavailable')
    r=verify(manager)
    assert r['status']=='failed'
    assert 'launch denied' in r['execution_error']
    assert 'cleanup unavailable' in r['cleanup_error']
