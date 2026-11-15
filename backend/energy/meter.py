"""Local RAPL package-energy server; CPU attribution remains a client estimate.

Only package domains are summed, so nested core domains are not double counted.
These readings measure host package energy during an interval, not energy solely
attributable to the diagnostic process.
"""
from __future__ import annotations

import json
import math
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from pyJoules.energy_meter import EnergyMeter
from pyJoules.device.rapl_device import RaplDevice


class EnergyServer:
    def __init__(self, host='127.0.0.1', port=5001):
        self.host, self.port = host, port
        self.server = self.thread = self._meter = None
        self._started = None
        self._lock = threading.Lock()

    def begin_measurement(self):
        with self._lock:
            if self._meter is None:
                raise RuntimeError('RAPL package counters unavailable')
            if self._started is not None:
                raise RuntimeError('A measurement is already active')
            self._meter.start(tag='infrasage')
            self._started = time.monotonic()
            return {'status': 'started', 'method': 'measured'}

    def end_measurement(self):
        with self._lock:
            if self._started is None:
                raise RuntimeError('No active measurement')
            started, self._started = self._started, None
            self._meter.stop()
            elapsed = time.monotonic() - started
            samples = list(self._meter.get_trace())
            values = [value for sample in samples for value in sample.energy.values()]
            if not values or any(not math.isfinite(v) or v < 0 for v in values):
                raise RuntimeError('RAPL returned missing or invalid energy')
            return {'energy_microjoules': math.fsum(values), 'elapsed_seconds': elapsed,
                    'method': 'measured', 'scope': 'host_cpu_packages'}

    def start(self):
        try:
            device = RaplDevice()
            domains = device.available_package_domains()
            if domains:
                device.configure(domains)
                self._meter = EnergyMeter([device])
        except Exception:
            self._meter = None
        self.server = ThreadingHTTPServer((self.host, self.port), self._make_handler())
        self.port = self.server.server_port
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def stop(self):
        if self.server:
            self.server.shutdown()
            self.server.server_close()
            if self.thread:
                self.thread.join(timeout=5)

    def _make_handler(self):
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def _json(self, value, status=200):
                body = json.dumps(value, allow_nan=False).encode()
                self.send_response(status)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Content-Length', str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self):
                if self.path == '/health':
                    self._json({'status': 'ok', 'rapl_available': owner._meter is not None})
                else:
                    self._json({'error': 'Not found'}, 404)

            def do_POST(self):
                action = {'/measure/start': owner.begin_measurement,
                          '/measure/stop': owner.end_measurement}.get(self.path)
                if action is None:
                    self._json({'error': 'Not found'}, 404)
                    return
                try:
                    self._json(action())
                except Exception as error:
                    self._json({'error': str(error)}, 503)

        return Handler


def run_server(host='127.0.0.1', port=5001):
    server = EnergyServer(host, port)
    server.start()
    return server


if __name__ == '__main__':
    import signal

    server = run_server()
    stopped = threading.Event()
    signal.signal(signal.SIGINT, lambda *_: stopped.set())
    signal.signal(signal.SIGTERM, lambda *_: stopped.set())
    stopped.wait()
    server.stop()
