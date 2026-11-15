"""Energy units, interval accounting, and honest fallback behavior."""
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
import requests

from backend.energy import meter
from backend.twin import energy_client


def fake_response(data, status_code=200):
    response = MagicMock()
    response.status_code = status_code
    response.json.return_value = data
    return response


def test_server_reads_rapl_trace_in_microjoules(monkeypatch):
    server = meter.EnergyServer()
    server._meter = MagicMock()
    server._meter.get_trace.return_value = [
        SimpleNamespace(energy={'package_0': 2_000_000}),
        SimpleNamespace(energy={'package_0': 1_000_000}),
    ]
    monkeypatch.setattr(meter.time, 'monotonic', MagicMock(side_effect=[10.0, 12.0]))

    started = server.begin_measurement()
    reading = server.end_measurement()

    assert started == {'status': 'started', 'method': 'measured'}
    server._meter.start.assert_called_once_with(tag='infrasage')
    server._meter.stop.assert_called_once_with()
    server._meter.get_trace.assert_called_once_with()
    assert reading['energy_microjoules'] == 3_000_000
    assert reading['elapsed_seconds'] == 2.0
    assert reading['method'] == 'measured'


def test_server_refuses_overlapping_measurements(monkeypatch):
    server = meter.EnergyServer()
    server._meter = MagicMock()
    monkeypatch.setattr(meter.time, 'monotonic', lambda: 10.0)
    server.begin_measurement()
    with pytest.raises(RuntimeError):
        server.begin_measurement()
    server._meter.start.assert_called_once_with(tag='infrasage')


def test_server_refuses_stop_without_start():
    server = meter.EnergyServer()
    server._meter = MagicMock()
    with pytest.raises(RuntimeError):
        server.end_measurement()
    server._meter.stop.assert_not_called()


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(energy_client.EnergyClient, '_collect_hardware_context', lambda self: {})
    monkeypatch.setattr(energy_client, '_DEFAULT_CPU_TDP_WATTS', 65.0)
    return energy_client.EnergyClient()


def test_unavailable_server_estimate_covers_diagnostic_interval(client, monkeypatch):
    monkeypatch.setattr(energy_client.time, 'monotonic', MagicMock(side_effect=[10.0, 12.0]))
    monkeypatch.setattr(energy_client.requests, 'post', MagicMock(side_effect=requests.ConnectionError('offline')))
    monkeypatch.setattr(energy_client.psutil, 'cpu_percent', lambda **kwargs: 50.0)

    assert client.start_measurement() is False
    reading = client.stop_measurement()

    assert reading.energy_method == 'estimated'
    assert reading.energy_joules == pytest.approx(65.0)
    assert reading.elapsed_seconds == pytest.approx(2.0)


def test_successful_server_reading_preserves_method_and_converts_units(client, monkeypatch):
    monkeypatch.setattr(energy_client.time, 'monotonic', MagicMock(side_effect=[10.0, 12.0]))
    post = MagicMock(side_effect=[
        fake_response({'status': 'started', 'method': 'measured'}),
        fake_response({'energy_microjoules': 2_000_000, 'elapsed_seconds': 2.0, 'method': 'measured'}),
    ])
    monkeypatch.setattr(energy_client.requests, 'post', post)

    assert client.start_measurement() is True
    reading = client.stop_measurement()

    assert reading.energy_method == 'measured'
    assert reading.energy_joules == pytest.approx(2.0)
    assert reading.elapsed_seconds == pytest.approx(2.0)
    assert post.call_count == 2


def test_server_failure_after_start_uses_elapsed_interval_estimate(client, monkeypatch):
    monkeypatch.setattr(energy_client.time, 'monotonic', MagicMock(side_effect=[10.0, 12.0]))
    monkeypatch.setattr(energy_client.requests, 'post', MagicMock(side_effect=[
        fake_response({'status': 'started', 'method': 'measured'}),
        requests.ConnectionError('server went away'),
    ]))
    monkeypatch.setattr(energy_client.psutil, 'cpu_percent', lambda **kwargs: 50.0)

    assert client.start_measurement() is True
    reading = client.stop_measurement()

    assert reading.energy_method == 'estimated'
    assert reading.energy_joules == pytest.approx(65.0)
    assert reading.elapsed_seconds == pytest.approx(2.0)


def test_server_configures_package_domains_without_nested_core_counters(monkeypatch):
    device = MagicMock()
    device.available_package_domains.return_value = ['package_0', 'package_1']
    monkeypatch.setattr(meter, 'RaplDevice', lambda: device)
    create_meter = MagicMock()
    monkeypatch.setattr(meter, 'EnergyMeter', create_meter)
    http = MagicMock()
    http.server_port = 5001
    monkeypatch.setattr(meter, 'ThreadingHTTPServer', lambda *args: http)
    monkeypatch.setattr(meter.threading, 'Thread', MagicMock())

    server = meter.EnergyServer()
    server.start()
    server.stop()

    device.configure.assert_called_once_with(['package_0', 'package_1'])
    create_meter.assert_called_once_with([device])
    device.available_domains.assert_not_called()
