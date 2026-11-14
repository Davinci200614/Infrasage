from copy import deepcopy
from datetime import datetime,timedelta,timezone
from dataclasses import replace

import pytest

from backend.core.models import ExecutionStatus
from backend.core.diagnostic_adapter import DiagnosticAdapter
from backend.diagnosis.catalogue_loader import load_catalogues
from backend.diagnosis.bayesian import apply_observation

CID='a'*64
OTHER='b'*64
NOW=datetime(2026,11,14,12,tzinfo=timezone.utc)
TARGET={'container_name':'redis-test','container_id':CID}


@pytest.fixture(scope='module')
def test():return load_catalogues(research_mode=True).tests['redis_runtime_state']


@pytest.fixture
def raw():
    stamp=NOW.isoformat()
    return {'test_id':'redis_runtime_state','source':'docker','status':'completed',
            'target':dict(TARGET),'target_identifier':'docker:'+CID,
            'data':{'id':CID,'name':'redis-test','status':'running'},
            'observed_at':stamp,'retrieved_at':stamp,'duration_seconds':.01,
            'docker_endpoint':'http+docker://localhost',
            'binding':{'provider':'docker','operation':'container_process_state',**TARGET,
                       'docker_endpoint':'http+docker://localhost','verified_at':stamp,'freshness_max_seconds':15}}


def adapt(test,raw):return DiagnosticAdapter().adapt_with_metadata(test,raw,TARGET,retrieved_at=NOW)


@pytest.mark.parametrize('state,outcome',[('running','running'),('exited','stopped'),('restarting','restarting')])
def test_state_mapping_timestamp_provenance(test,raw,state,outcome):
    raw['data']['status']=state
    r=adapt(test,raw)
    assert r.observation.execution_status==ExecutionStatus.COMPLETED
    assert r.observation.observed_outcome==outcome
    assert r.observation.timestamp==NOW
    assert r.observation.target_identifier=='docker:'+CID
    assert r.metadata['execution_result']==raw
    assert r.metadata['identity_verified']
    assert r.observation.measured_energy_joules is None


@pytest.mark.parametrize('mutation',[
    lambda r:r.update(test_id='worker_runtime_state'),
    lambda r:r.pop('test_id'),
    lambda r:r['target'].update(container_id=OTHER),
    lambda r:r['data'].update(id=OTHER),
    lambda r:r['data'].update(name='other'),
    lambda r:r.update(target_identifier='docker:'+OTHER),
    lambda r:r.pop('target'),
    lambda r:r.update(source='netdata'),
    lambda r:r.update(error='Provider permission failure'),
    lambda r:r.update(errors={'docker':'denied'}),
    lambda r:r.update(binding=True),
    lambda r:r['binding'].update(container_name='other'),
    lambda r:r['binding'].update(docker_endpoint='other-daemon'),
])
def test_identity_errors_and_malformed_binding_never_complete(test,raw,mutation):
    mutation(raw)
    r=adapt(test,raw)
    assert r.observation.execution_status!=ExecutionStatus.COMPLETED
    assert r.observation.observed_outcome is None


@pytest.mark.parametrize('mutation',[
    lambda r:r.pop('observed_at'),
    lambda r:r.update(observed_at='invalid'),
    lambda r:r.update(observed_at=NOW.replace(tzinfo=None).isoformat()),
    lambda r:r.update(observed_at=(NOW-timedelta(seconds=16)).isoformat()),
    lambda r:r.update(observed_at=(NOW+timedelta(seconds=1)).isoformat()),
    lambda r:r.update(retrieved_at=(NOW-timedelta(seconds=1)).isoformat()),
    lambda r:r['binding'].update(verified_at=(NOW-timedelta(seconds=16)).isoformat()),
])
def test_stale_missing_and_inconsistent_times(test,raw,mutation):
    mutation(raw)
    r=adapt(test,raw)
    assert r.observation.execution_status==ExecutionStatus.UNAVAILABLE
    assert r.observation.observed_outcome is None
    assert r.metadata['observation_timestamp_role']=='processing_attempt'
    assert r.metadata['execution_result']==raw


@pytest.mark.parametrize('status',['failed','unavailable'])
def test_unsuccessful_execution_keeps_beliefs(test,raw,status):
    raw.update(status=status,error='Docker unavailable')
    r=adapt(test,raw)
    prior=load_catalogues(research_mode=True).initial_prior
    assert apply_observation(prior,test,r.observation)==prior
    assert r.metadata['execution_result']['error']=='Docker unavailable'


@pytest.mark.parametrize('state',['paused','dead','created','removing','unknown',None])
def test_unsupported_states_do_not_become_stopped(test,raw,state):
    raw['data']['status']=state
    assert adapt(test,raw).observation.execution_status==ExecutionStatus.UNAVAILABLE


@pytest.mark.parametrize('field,value',[('duration_seconds',float('nan')),('duration_seconds',-1),('measured_energy_joules',float('inf'))])
def test_invalid_measurements(test,raw,field,value):
    raw[field]=value
    assert adapt(test,raw).observation.execution_status==ExecutionStatus.FAILED


def test_no_missing_counter_zero_fallback():
    assert DiagnosticAdapter()._resolve_worker_errors({'error_signatures':{}},{}) is None


def test_existing_cpu_mapping_not_replaced(test,raw):
    cpu=load_catalogues(research_mode=True).tests['host_cpu_pressure']
    assert adapt(cpu,raw).observation.execution_status==ExecutionStatus.FAILED


def test_reject_missing_target(test,raw):
    with pytest.raises(ValueError):DiagnosticAdapter().adapt(test,raw,{})


def test_completed_evidence_updates_existing_engine(test,raw):
    r=adapt(test,raw)
    prior=load_catalogues(research_mode=True).initial_prior
    posterior=apply_observation(prior,test,r.observation)
    assert posterior!=prior
    assert sum(posterior.values())==pytest.approx(1)
