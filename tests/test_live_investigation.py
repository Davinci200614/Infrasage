from copy import deepcopy
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from backend.core.collector_manager import CollectorManager
from backend.core.diagnostic_adapter import DiagnosticAdapter
from backend.core.models import Decision, ExecutionStatus
from backend.core.orchestrator import (LiveInvestigationConfig, StoppingConfig,
                                       run_live_investigation, run_investigation)
from backend.diagnosis.catalogue_loader import load_catalogues
from backend.optimization.planner import DiagnosticPolicy

RID='a'*64
WID='b'*64
HELPER='sha256:'+'c'*64
RUNTIME='redis_runtime_state'
TCP='worker_redis_connectivity'
PROBE=dict(redis_host='redis',redis_port=6379,helper_image=HELPER,connection_timeout=.3,execution_deadline=5.)


@pytest.fixture
def loaded():return load_catalogues(research_mode=True)


@pytest.fixture
def config():
    return LiveInvestigationConfig({'container_id':RID},{'container_id':WID},dict(PROBE))


def raw_result(tid, outcome=None, *, stamp=None, cid=None):
    cid=cid or (RID if tid==RUNTIME else WID)
    name='redis' if tid==RUNTIME else 'worker'
    stamp=stamp or datetime.now(timezone.utc).isoformat()
    binding={'provider':'docker','operation':'container_process_state' if tid==RUNTIME else 'worker_to_redis_tcp',
             'container_id':cid,'container_name':name,'docker_endpoint':'http+docker://localhost',
             'verified_at':stamp,'freshness_max_seconds':15}
    raw={'test_id':tid,'source':'docker','status':'completed','target':{'container_id':cid,'container_name':name},
         'target_identifier':'docker:'+cid,'data':{'id':cid,'name':name},'observed_at':stamp,'retrieved_at':stamp,
         'duration_seconds':.01,'docker_endpoint':'http+docker://localhost','binding':binding,
         'action_phase':'binding_verification_with_evidence','diagnostic_actions':1}
    if tid==RUNTIME:
        raw['data']['status']=outcome or 'running'
    else:
        binding.update(PROBE)
        raw.update(probe_config=dict(PROBE),namespace='container:'+cid,helper_image=HELPER,helper_removed=True,request_id='nonce')
        raw['data'].update(outcome=outcome or 'connected',endpoint={'host':'redis','port':6379})
        raw['probe_result']={'protocol':'infrasage-tcp-v1','worker_id':cid,'request_id':'nonce','status':'completed',
                             'outcome':outcome or 'connected','endpoint':{'host':'redis','port':6379},'observed_at':stamp}
    return raw


@pytest.fixture
def manager(loaded):
    m=CollectorManager(loaded)
    m.execute_investigation_test=Mock(side_effect=lambda test,target,**kw:raw_result(test.identifier))
    return m


def run(loaded,manager,config,**kwargs):
    return run_live_investigation(loaded,manager,config,**kwargs)


def test_real_planner_selects_dispatches_and_updates(loaded,manager,config):
    r=run(loaded,manager,config)
    assert r.result.decision==Decision.ABSTAIN
    assert [s.test.identifier for s in r.result.selections]==[RUNTIME,TCP]
    assert [o.test_identifier for o in r.result.observations]==[RUNTIME,TCP]
    assert len(r.evidence_records)==2
    assert len(r.result.belief_history)==3
    assert r.result.final_beliefs!=loaded.initial_prior
    assert sum(r.result.final_beliefs.values())==pytest.approx(1)
    assert r.result.total_estimated_energy_joules==pytest.approx(.55)
    assert r.result.total_estimated_latency_seconds==pytest.approx(.37)
    assert manager.execute_investigation_test.call_args_list[0].args[1]==config.redis_target
    assert manager.execute_investigation_test.call_args_list[1].args[1]==config.worker_target
    assert r.trace[0]['event']=='started' and r.trace[-1]['event']=='stopped'
    for event in r.trace:
        if event['event']=='selected':assert event['information_gain_bits']>0


def test_fixed_policy_changes_selected_target_without_preexecution(loaded,manager,config):
    r=run(loaded,manager,config,policy=DiagnosticPolicy('fixed',(TCP,RUNTIME)),stopping=StoppingConfig(max_tests=1))
    manager.execute_investigation_test.assert_called_once()
    assert manager.execute_investigation_test.call_args.args[0].identifier==TCP
    assert manager.execute_investigation_test.call_args.args[1]==config.worker_target
    assert r.result.reason=='maximum test count reached'


@pytest.mark.parametrize('field,value,expected',[
    ('redis_target',None,[TCP]),('worker_target',None,[RUNTIME]),('probe_config',None,[RUNTIME]),
    ('probe_config',{**PROBE,'helper_image':'python:latest'},[RUNTIME]),
    ('redis_target',{'container_id':'abc'},[TCP])])
def test_eligibility_filters_missing_configuration(loaded,manager,config,field,value,expected):
    r=run(loaded,manager,replace(config,**{field:value}))
    assert [s.test.identifier for s in r.result.selections]==expected


def test_no_configuration_no_io(loaded,manager):
    r=run(loaded,manager,LiveInvestigationConfig())
    assert r.result.reason=='no remaining usable tests'
    assert r.result.final_beliefs==loaded.initial_prior
    manager.execute_investigation_test.assert_not_called()


@pytest.mark.parametrize('status',['failed','unavailable'])
def test_failures_charge_attempts_without_updating(loaded,manager,config,status,monkeypatch):
    manager.execute_investigation_test.side_effect=lambda test,target,**kw:{'test_id':test.identifier,'status':status,'error':'provider denied'}
    updater=Mock(side_effect=AssertionError('Failure must not reach Bayes'))
    monkeypatch.setattr('backend.core.orchestrator.apply_observation',updater)
    r=run(loaded,manager,config)
    assert r.result.final_beliefs==loaded.initial_prior
    assert len(r.result.observations)==2 and not r.evidence_records
    assert r.result.total_estimated_energy_joules==pytest.approx(.55)
    assert manager.execute_investigation_test.call_count==2
    updater.assert_not_called()


def test_dispatch_exception_does_not_retry(loaded,manager,config):
    manager.execute_investigation_test.side_effect=RuntimeError('provider crashed')
    r=run(loaded,manager,config)
    assert len(r.result.observations)==2
    assert all(o.execution_status==ExecutionStatus.FAILED for o in r.result.observations)
    assert r.result.final_beliefs==loaded.initial_prior


@pytest.mark.parametrize('budget,reason',[(0,'maximum test count reached'),(1,'maximum test count reached')])
def test_attempt_budget(loaded,manager,config,budget,reason):
    r=run(loaded,manager,config,stopping=StoppingConfig(max_tests=budget))
    assert len(r.result.observations)==budget
    assert manager.execute_investigation_test.call_count==budget
    assert r.result.reason==reason


def test_energy_budget_prevents_unaffordable_diagnostics(loaded,manager,config):
    r=run(loaded,manager,config,stopping=StoppingConfig(max_energy_joules=.09))
    assert r.result.reason=='energy budget exhausted'
    manager.execute_investigation_test.assert_not_called()
    assert r.result.total_estimated_energy_joules==0


def test_confidence_rule_stops_after_first_evidence(loaded,manager,config):
    manager.execute_investigation_test.side_effect=lambda test,target,**kw:raw_result(test.identifier,'exited')
    r=run(loaded,manager,config,stopping=StoppingConfig(confidence_threshold=.5))
    assert r.result.decision==Decision.DIAGNOSED
    assert r.result.diagnosed_hypothesis=='redis_process_stopped'
    manager.execute_investigation_test.assert_called_once()


def test_initial_evidence_consumed_once_with_cost(loaded,manager,config):
    initial=DiagnosticAdapter().adapt_with_metadata(loaded.tests[RUNTIME],raw_result(RUNTIME),config.redis_target)
    r=run(loaded,manager,config,initial_evidence=[initial,initial])
    assert manager.execute_investigation_test.call_count==1
    assert manager.execute_investigation_test.call_args.args[0].identifier==TCP
    assert len(r.evidence_records)==2
    assert len(r.result.observations)==2
    assert len(r.rejected_evidence)==1
    assert r.result.total_estimated_energy_joules==pytest.approx(.55)


def test_stale_initial_evidence_not_admitted_allows_fresh_test(loaded,manager,config):
    now=datetime.now(timezone.utc)-timedelta(seconds=16)
    raw=raw_result(RUNTIME,stamp=now.isoformat())
    initial=DiagnosticAdapter().adapt_with_metadata(loaded.tests[RUNTIME],raw,config.redis_target,retrieved_at=now)
    r=run(loaded,manager,config,initial_evidence=[initial])
    assert len(r.evidence_records)==2
    assert manager.execute_investigation_test.call_count==2
    assert r.rejected_evidence


def test_tampered_initial_outcome_rejected(loaded,manager,config):
    initial=DiagnosticAdapter().adapt_with_metadata(loaded.tests[RUNTIME],raw_result(RUNTIME),config.redis_target)
    initial=replace(initial,observation=replace(initial.observation,observed_outcome='stopped'))
    r=run(loaded,manager,config,initial_evidence=[initial])
    assert manager.execute_investigation_test.call_count==2
    assert 'differs' in r.rejected_evidence[0]['reason']


def test_evidence_groups_prevent_correlated_alias_selection(loaded,manager,config):
    loaded.test_metadata[TCP]['evidence_group']=loaded.test_metadata[RUNTIME]['evidence_group']
    r=run(loaded,manager,config)
    assert len(r.evidence_records)==1
    manager.execute_investigation_test.assert_called_once()
    assert r.result.reason=='no remaining usable tests'


def test_duplicate_underlying_measurement_rejected_across_tests(loaded,manager,config):
    shared=datetime.now(timezone.utc).isoformat()
    config=replace(config,worker_target={'container_id':RID})
    manager.execute_investigation_test.side_effect=lambda test,target,**kw:raw_result(test.identifier,stamp=shared,cid=RID)
    r=run(loaded,manager,config)
    assert len(r.result.observations)==2 and len(r.evidence_records)==1
    assert r.rejected_evidence[-1]['reason']=='duplicate underlying measurement'


@pytest.mark.parametrize('mutation',[
    lambda raw:raw.update(test_id='other'),
    lambda raw:raw.update(target_identifier='docker:'+'e'*64),
    lambda raw:raw.update(observed_at=(datetime.now(timezone.utc)-timedelta(seconds=20)).isoformat()),
    lambda raw:raw['data'].update(status='not_an_outcome'),
])
def test_invalid_evidence_never_changes_prior(loaded,manager,config,mutation):
    def execute(test,target,**kwargs):
        raw=raw_result(test.identifier);mutation(raw);return raw
    manager.execute_investigation_test.side_effect=execute
    r=run(loaded,manager,replace(config,worker_target=None))
    assert r.result.final_beliefs==loaded.initial_prior
    assert not r.evidence_records


def test_overall_deadline_filters_without_io(loaded,manager,config):
    r=run(loaded,manager,replace(config,maximum_duration_seconds=0))
    assert r.result.decision==Decision.ABSTAIN and 'deadline' in r.result.reason
    manager.execute_investigation_test.assert_not_called()


def test_no_probe_if_its_configured_budget_does_not_fit(loaded,manager,config):
    r=run(loaded,manager,replace(config,maximum_duration_seconds=4))
    assert [s.test.identifier for s in r.result.selections]==[RUNTIME]
    assert 'deadline' in r.result.reason


def test_late_selected_result_rejected(loaded,manager,config,monkeypatch):
    clock=[0.]
    monkeypatch.setattr('time.monotonic',lambda:clock[0])
    def late(test,target,**kwargs):
        raw=raw_result(test.identifier);clock[0]=31.;return raw
    manager.execute_investigation_test.side_effect=late
    r=run(loaded,manager,config)
    assert not r.evidence_records and r.result.final_beliefs==loaded.initial_prior
    manager.execute_investigation_test.assert_called_once()
    assert 'deadline' in r.result.reason


def test_manager_capability_checks_never_open_provider(loaded,config,monkeypatch):
    monkeypatch.setattr('backend.core.collector_manager.DockerCollector',Mock(side_effect=AssertionError('No provider I/O')))
    manager=CollectorManager(loaded)
    for tid,target in [(RUNTIME,config.redis_target),(TCP,config.worker_target)]:
        check=manager.check_test_capability(tid,target,probe_config=PROBE,deadline_seconds=5)
        assert check['eligible'] and not check['verification_performed']
    assert not manager.check_test_capability('host_cpu_pressure',config.redis_target,deadline_seconds=3)['eligible']


def test_selected_verification_is_only_action_and_evidence(loaded,config):
    provider=SimpleNamespace(client=SimpleNamespace(api=SimpleNamespace(timeout=60)))
    manager=CollectorManager(loaded,docker_collector=provider)
    manager.verify_runtime_binding=Mock(side_effect=lambda test,target:raw_result(RUNTIME))
    manager.execute_test=Mock(side_effect=AssertionError('Must not repeat verification'))
    r=run(loaded,manager,config,stopping=StoppingConfig(max_tests=1))
    manager.verify_runtime_binding.assert_called_once()
    manager.execute_test.assert_not_called()
    assert len(r.result.observations)==1 and len(r.evidence_records)==1
    assert provider.client.api.timeout==60
    assert r.result.total_estimated_energy_joules==pytest.approx(.1)


def test_same_catalogue_instance_required(loaded,manager,config):
    with pytest.raises(ValueError,match='same loaded catalogue'):
        run_live_investigation(load_catalogues(research_mode=True),manager,config)


def test_replay_remains_deterministic():
    from backend.diagnosis.example import synthetic_model
    prior,tests=synthetic_model()
    a=run_investigation(prior,tests,DiagnosticPolicy('ratio'),'redis_unavailable',seed=7)
    b=run_investigation(prior,tests,DiagnosticPolicy('ratio'),'redis_unavailable',seed=7)
    assert a==b


def test_selected_tcp_verification_not_reexecuted(loaded,config):
    provider=SimpleNamespace(client=SimpleNamespace(api=SimpleNamespace(timeout=60)))
    manager=CollectorManager(loaded,docker_collector=provider)
    manager.verify_connectivity_binding=Mock(side_effect=lambda test,target,**kw:raw_result(TCP))
    manager.execute_test=Mock(side_effect=AssertionError('Do not repeat the verification probe'))
    r=run(loaded,manager,config,policy=DiagnosticPolicy('fixed',(TCP,RUNTIME)),stopping=StoppingConfig(max_tests=1))
    manager.verify_connectivity_binding.assert_called_once()
    assert manager.verify_connectivity_binding.call_args.kwargs['_deadline_at']>0
    manager.execute_test.assert_not_called()
    assert len(r.evidence_records)==1
    assert r.result.total_estimated_energy_joules==pytest.approx(.45)
    assert provider.client.api.timeout==60


def test_selected_provider_exception_restores_request_timeout(loaded,config):
    provider=SimpleNamespace(client=SimpleNamespace(api=SimpleNamespace(timeout=60)))
    manager=CollectorManager(loaded,docker_collector=provider)
    manager.verify_runtime_binding=Mock(side_effect=RuntimeError('provider failed'))
    r=run(loaded,manager,replace(config,worker_target=None))
    assert provider.client.api.timeout==60
    assert r.result.observations[0].execution_status==ExecutionStatus.FAILED
    assert r.result.final_beliefs==loaded.initial_prior


def test_unknown_leader_abstains_using_original_stopping_rule(loaded,manager,config):
    old_unknown=loaded.initial_prior['other_unknown']
    loaded.initial_prior.update({h:.9 if h=='other_unknown' else p*.1/(1-old_unknown)
                                 for h,p in loaded.initial_prior.items()})
    manager.execute_investigation_test.side_effect=lambda test,target,**kwargs:raw_result(test.identifier,'exited')
    r=run(loaded,manager,config,policy=DiagnosticPolicy('fixed',(RUNTIME,TCP)))
    assert r.result.decision==Decision.ABSTAIN
    assert r.result.reason=='other_unknown is most probable'
    manager.execute_investigation_test.assert_called_once()


def test_uninformative_tests_stop_before_execution(loaded,manager,config):
    for tid in (RUNTIME,TCP):
        test=loaded.tests[tid]
        loaded.tests[tid]=replace(test,likelihoods={h:{o:1/len(test.outcomes) for o in test.outcomes} for h in loaded.initial_prior})
    r=run(loaded,manager,config)
    assert 'no informative' in r.result.reason
    manager.execute_investigation_test.assert_not_called()


def test_exact_energy_boundary_allows_one_test(loaded,manager,config):
    r=run(loaded,manager,config,stopping=StoppingConfig(max_energy_joules=.1))
    assert len(r.result.observations)==1
    assert r.result.reason=='energy budget exhausted'
    assert r.result.total_estimated_energy_joules==.1


def test_initial_record_cannot_bypass_attempt_budget(loaded,manager,config):
    initial=DiagnosticAdapter().adapt_with_metadata(loaded.tests[RUNTIME],raw_result(RUNTIME),config.redis_target)
    r=run(loaded,manager,config,initial_evidence=[initial],stopping=StoppingConfig(max_tests=0))
    assert not r.result.observations and not r.evidence_records
    assert r.result.final_beliefs==loaded.initial_prior
    manager.execute_investigation_test.assert_not_called()
