from dataclasses import replace
from datetime import datetime, timezone
import json

import pytest
import requests

from backend.collectors.netdata import NetdataCollector
from backend.core.models import ExecutionStatus, Observation, validate_distribution
from backend.diagnosis.bayesian import apply_observation
from backend.diagnosis.catalogue_loader import load_catalogues
from backend.processing.normalizer import normalize_cpu_observation
from scripts.demo_real_observation import main

NOW = datetime(2026, 11, 14, 12, tzinfo=timezone.utc)
CPU = {'time':NOW.timestamp(), 'guest_nice':0, 'guest':0, 'steal':0,
       'softirq':.2, 'irq':0, 'user':15, 'system':8, 'nice':0, 'iowait':3}


@pytest.fixture
def netdata_response(monkeypatch):
    calls = []
    def get(session, url, params=None, timeout=None):
        calls.append((url, params, timeout))
        response = requests.Response()
        response.url = url
        response.status_code = 200
        if url.endswith('/charts'):
            payload = {'charts':{'net.docker0':{}}}
        elif params['chart'] == 'system.cpu':
            payload = {'labels':list(CPU), 'data':[list(CPU.values())]}
        elif params['chart'] in ('net_speed.docker0', 'net_duplex.docker0'):
            response.status_code = 404
            payload = {}
        else:
            payload = {'labels':['time','value'], 'data':[[NOW.timestamp(),0]]}
        response._content = json.dumps(payload).encode()
        return response
    monkeypatch.setattr(requests.Session, 'get', get)
    return calls


def test_existing_collector_to_bayes(netdata_response):
    loaded = load_catalogues(research_mode=True)
    collector = NetdataCollector('http://127.0.0.1:19999')
    try:
        raw = collector.collect()
    finally:
        collector.session.close()
    assert raw['cpu'] == CPU
    assert {'net_speed.docker0','net_duplex.docker0'} <= raw['errors'].keys()
    test = loaded.tests['host_cpu_pressure']
    normalized = normalize_cpu_observation(raw, test, loaded.test_metadata[test.identifier], 'host', retrieved_at=NOW)
    assert normalized.observation.execution_status == ExecutionStatus.COMPLETED
    posterior = apply_observation(loaded.initial_prior, test, normalized.observation)
    assert posterior != loaded.initial_prior
    validate_distribution(posterior)
    # Independently check the loaded test's row is the one consumed by the engine.
    weights = {h:p*test.likelihoods[h]['normal'] for h,p in loaded.initial_prior.items()}
    assert posterior == pytest.approx({h:w/sum(weights.values()) for h,w in weights.items()})
    assert netdata_response[0][1] == {'chart':'system.cpu','after':-5,'points':1}


@pytest.mark.parametrize('status', [ExecutionStatus.FAILED,ExecutionStatus.UNAVAILABLE])
def test_failed_unavailable_do_not_update(status):
    loaded = load_catalogues(research_mode=True)
    obs = Observation('host_cpu_pressure','host',status)
    assert apply_observation(loaded.initial_prior,loaded.tests['host_cpu_pressure'],obs) == loaded.initial_prior


def test_identity_and_outcome_validation():
    loaded = load_catalogues(research_mode=True)
    obs = Observation('host_cpu_pressure','host',ExecutionStatus.COMPLETED,'normal')
    test = loaded.tests['host_cpu_pressure']
    with pytest.raises(ValueError, match='identifier mismatch'):
        apply_observation(loaded.initial_prior,test,replace(obs,test_identifier='wrong_test'))
    with pytest.raises(ValueError, match='Unsupported outcome'):
        apply_observation(loaded.initial_prior,test,replace(obs,observed_outcome='hot'))


def test_cli_uses_existing_collector_and_reports_synthetic(monkeypatch, capsys, netdata_response):
    # Match retrieval time to the deterministic recorded CPU row. No live I/O.
    class Clock:
        @staticmethod
        def now(tz):
            return NOW
    monkeypatch.setattr('scripts.demo_real_observation.datetime',Clock)
    assert main([]) == 0
    output = json.loads(capsys.readouterr().out)
    assert output['model']['status'] == 'synthetic_provisional_not_calibrated'
    assert output['live_execution_enabled'] is False
    assert output['monitoring']['cpu_utilization_percent'] == pytest.approx(23.2)
    assert output['updated_posterior'] != output['initial_prior']
    assert output['posterior_sum'] == pytest.approx(1)
    assert netdata_response


def test_cli_network_failure(monkeypatch,capsys):
    def fail(*args,**kwargs):
        raise requests.ConnectionError('Agent unreachable')
    monkeypatch.setattr(requests.Session,'get',fail)
    assert main([]) == 1
    output = json.loads(capsys.readouterr().out)
    assert output['observation']['execution_status'] == 'failed'
    assert output['initial_prior'] == output['updated_posterior']
    assert 'Agent unreachable' in output['monitoring']['collector_errors']['cpu']
    assert output['monitoring']['cpu_utilization_percent'] is None
