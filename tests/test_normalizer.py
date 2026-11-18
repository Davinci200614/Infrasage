from copy import deepcopy
from dataclasses import replace
from datetime import datetime, timezone

import pytest

from backend.core.models import ExecutionStatus
from backend.diagnosis.catalogue_loader import load_catalogues
from backend.processing.normalizer import CPU_ACTIVITY_DIMENSIONS, normalize_cpu_observation

NOW = datetime(2026, 11, 14, 12, tzinfo=timezone.utc)


def snapshot(value=20):
    return {'source':'netdata', 'cpu':dict.fromkeys(CPU_ACTIVITY_DIMENSIONS, 0) | {'user':value, 'iowait':0, 'time':NOW.timestamp()}, 'errors':{}}


@pytest.fixture(scope='module')
def loaded():
    return load_catalogues(research_mode=True)


def normalize(data, loaded, **kwargs):
    return normalize_cpu_observation(data, loaded.tests['host_cpu_pressure'],
        loaded.test_metadata['host_cpu_pressure'], 'linux-host-01', retrieved_at=NOW, **kwargs)


@pytest.mark.parametrize('value,outcome', [(0,'normal'), (69.999,'normal'), (70,'high'), (89.999,'high'), (90,'critical'), (100,'critical')])
def test_thresholds(value, outcome, loaded):
    r = normalize(snapshot(value), loaded)
    assert r.observation.execution_status == ExecutionStatus.COMPLETED
    assert r.observation.observed_outcome == outcome
    assert r.metadata['cpu_utilization_percent'] == value
    assert r.observation.timestamp == NOW
    assert r.observation.measured_energy_joules is None
    assert r.metadata['actual_window'] is None


def test_netdata_disjoint_guest_and_iowait(loaded):
    data = snapshot(60)
    data['cpu'].update(guest=10, guest_nice=5, iowait=15)
    r = normalize(data, loaded)
    assert r.metadata['cpu_utilization_percent'] == 75
    assert r.observation.observed_outcome == 'high'
    assert r.metadata['iowait_percent'] == 15


@pytest.mark.parametrize('data,status,reason', [
    (None, ExecutionStatus.FAILED, 'malformed_collector_response'),
    ({'source':'other'}, ExecutionStatus.FAILED, 'malformed_collector_response'),
    ({'source':'netdata','cpu':None}, ExecutionStatus.UNAVAILABLE, 'missing_cpu_measurement'),
    ({'source':'netdata','cpu':[]}, ExecutionStatus.FAILED, 'unsupported_cpu_shape'),
    ({'source':'netdata','cpu':None,'errors':{'cpu':'HTTP timeout'}}, ExecutionStatus.FAILED, 'cpu_collector_failure'),
    ({'source':'netdata','cpu':None,'errors':{'cpu':'404 Client Error: Not Found'}}, ExecutionStatus.UNAVAILABLE, 'cpu_unavailable'),
    ({'source':'netdata','cpu':None,'errors':{'cpu':'No data available'}}, ExecutionStatus.UNAVAILABLE, 'cpu_unavailable'),
])
def test_missing_malformed_and_provider_failure(data, status, reason, loaded):
    r = normalize(data, loaded)
    assert r.observation.execution_status == status
    assert r.observation.observed_outcome is None
    assert r.metadata['reason'] == reason


@pytest.mark.parametrize('value', [float('nan'), float('inf'), -1, 101, '20', True])
def test_invalid_numeric(value, loaded):
    r = normalize(snapshot(value), loaded)
    assert r.observation.execution_status == ExecutionStatus.FAILED
    assert r.metadata['reason'] == 'invalid_cpu_numeric_value'


@pytest.mark.parametrize('missing', ['user','guest','system'])
def test_missing_dimensions(missing, loaded):
    data = snapshot()
    del data['cpu'][missing]
    assert normalize(data, loaded).observation.execution_status == ExecutionStatus.UNAVAILABLE


@pytest.mark.parametrize('stamp,reason', [(None,'missing_measurement_timestamp'), (NOW.timestamp()-16,'stale_cpu_evidence'), (NOW.timestamp()+1,'future_measurement_timestamp'), ('bad','invalid_measurement_timestamp'), (float('inf'),'invalid_measurement_timestamp'), (1e100,'invalid_measurement_timestamp')])
def test_timestamps(stamp, reason, loaded):
    data = snapshot()
    data['cpu']['time'] = stamp
    r = normalize(data, loaded)
    assert r.observation.observed_outcome is None
    assert r.metadata['reason'] == reason
    assert r.metadata['observation_timestamp_role'] == 'processing_attempt'
    assert r.observation.timestamp == NOW


def test_exact_freshness_boundary(loaded):
    data = snapshot()
    data['cpu']['time'] -= 15
    assert normalize(data, loaded).observation.execution_status == ExecutionStatus.COMPLETED


def test_optional_chart_errors_preserved(loaded):
    data = snapshot()
    data['errors'] = {'net_speed.docker0':'404 Client Error', 'net_duplex.docker0':'404 Client Error'}
    r = normalize(data, loaded)
    assert r.observation.execution_status == ExecutionStatus.COMPLETED
    assert r.metadata['collector_errors'] == data['errors']


def test_reject_wrong_test_and_vocabulary(loaded):
    test = loaded.tests['host_cpu_pressure']
    metadata = loaded.test_metadata[test.identifier]
    with pytest.raises(ValueError):
        normalize_cpu_observation(snapshot(), replace(test, identifier='other'), metadata, 'host')
    with pytest.raises(ValueError):
        normalize_cpu_observation(snapshot(), loaded.tests['worker_runtime_state'], metadata, 'host')


def test_no_derived_or_missing_zero_fallback(loaded):
    data = snapshot()
    data['cpu']['user'] = None
    data['derived'] = {'cpu':{'activity_percent':0}}
    assert normalize(data, loaded).observation.execution_status == ExecutionStatus.UNAVAILABLE


def test_unknown_dimensions_and_invalid_sum(loaded):
    data = snapshot()
    data['cpu']['unknown'] = 1
    assert normalize(data, loaded).metadata['reason'] == 'unsupported_cpu_dimensions'
    data = snapshot(80)
    data['cpu']['system'] = 30
    assert normalize(data, loaded).metadata['reason'] == 'invalid_cpu_total'


def test_invalid_catalogue_thresholds_and_window(loaded):
    metadata = deepcopy(loaded.test_metadata['host_cpu_pressure'])
    metadata['normalization']['thresholds']['high_min_percent'] = 95
    with pytest.raises(ValueError):
        normalize_cpu_observation(snapshot(), loaded.tests['host_cpu_pressure'], metadata, 'host')
    metadata = deepcopy(loaded.test_metadata['host_cpu_pressure'])
    metadata['normalization']['requested_window']['after_seconds'] = -60
    with pytest.raises(ValueError):
        normalize_cpu_observation(snapshot(), loaded.tests['host_cpu_pressure'], metadata, 'host')
