"""Energy measurement server for InfraSage (Phase 12).

Runs on port 5001, exposes /measure endpoint for per-test energy measurement.
Uses pyJoules for CPU energy estimation via RAPL/Intel Power Gadget.
"""

from __future__ import annotations

import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import urlparse

from pyJoules.energy_meter import EnergyMeter
from pyJoules.device.rapl_device import (
    RaplPackageDomain, RaplCoreDomain, RaplDramDomain, RaplDevice
)
from pyJoules.handler.csv_handler import CSVHandler


class EnergyServer:
    """Threaded energy measurement server."""

    def __init__(self, host: str = "127.0.0.1", port: int = 5001):
        self.host = host
        self.port = port
        self.server: ThreadingHTTPServer | None = None
        self.thread: threading.Thread | None = None
        self._meter: EnergyMeter | None = None
        self._device: RaplDevice | None = None
        self._running = False
        self._current_measurement: dict[str, Any] = {}

    def start(self) -> None:
        """Start the HTTP server in a background thread."""
        if self._running:
            return
        self._running = True

        # Initialize energy meter with RAPL device
        try:
            self._device = RaplDevice()
            domains = self._device.available_domains()
            if domains:
                self._device.configure(domains)
                self._meter = EnergyMeter([self._device])
            else:
                self._meter = None
        except Exception:
            # RAPL may not be available (e.g., VM, non-Intel CPU, no permissions)
            self._meter = None

        handler = self._make_handler()
        self.server = ThreadingHTTPServer((self.host, self.port), handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def stop(self) -> None:
        """Stop the HTTP server."""
        if self.server:
            self.server.shutdown()
            self.server.server_close()
            self._running = False
            if self.thread:
                self.thread.join(timeout=5)

    def _make_handler(self):
        server_ref = self

        class EnergyHandler(BaseHTTPRequestHandler):
            def log_message(self, *args):  # noqa: N802
                pass

            def do_GET(self):
                parsed = urlparse(self.path)
                if parsed.path == "/health":
                    self._json({"status": "ok", "rapl_available": server_ref._meter is not None})
                elif parsed.path == "/measure":
                    self._handle_measure()
                else:
                    self._error(404, "Not found")

            def do_POST(self):
                parsed = urlparse(self.path)
                if parsed.path == "/measure/start":
                    self._handle_measure_start()
                elif parsed.path == "/measure/stop":
                    self._handle_measure_stop()
                else:
                    self._error(404, "Not found")

            def _make_csv_handler(self):
                # Create a temp file for CSVHandler
                import tempfile
                tmp = tempfile.NamedTemporaryFile(mode='w', suffix='.csv', delete=False)
                tmp.close()
                return CSVHandler(tmp.name), tmp.name

            def _handle_measure_start(self):
                if not server_ref._meter:
                    self._error(503, "Energy meter not available (no RAPL or insufficient permissions - requires root)")
                    return
                try:
                    handler, tmp_file = self._make_csv_handler()
                    server_ref._meter.start(handler)
                    server_ref._current_measurement = {"handler": handler, "tmp_file": tmp_file, "started": time.time()}
                    self._json({"status": "started", "method": "rapl_estimate"})
                except PermissionError:
                    self._error(503, "RAPL access denied - run with root/sudo for energy measurement")
                except Exception as e:
                    self._error(500, str(e))

            def _handle_measure_stop(self):
                if not server_ref._meter:
                    self._error(503, "Energy meter not available (no RAPL)")
                    return
                if "handler" not in server_ref._current_measurement:
                    self._error(400, "No active measurement (call /measure/start first)")
                    return
                try:
                    handler = server_ref._current_measurement["handler"]
                    tmp_file = server_ref._current_measurement["tmp_file"]
                    server_ref._meter.stop()
                    energy = handler.get_data()
                    elapsed = time.time() - server_ref._current_measurement["started"]
                    server_ref._current_measurement = {}
                    # Clean up temp file
                    import os
                    try:
                        os.unlink(tmp_file)
                    except Exception:
                        pass
                    self._json({
                        "energy_microjoules": energy,
                        "elapsed_seconds": elapsed,
                        "method": "rapl_estimate"
                    })
                except PermissionError:
                    self._error(503, "RAPL access denied - run with root/sudo for energy measurement")
                except Exception as e:
                    self._error(500, str(e))

            def _handle_measure(self):
                """Legacy single-shot measure (deprecated)."""
                if not server_ref._meter:
                    self._error(503, "Energy meter not available (no RAPL)")
                    return
                try:
                    handler, tmp_file = self._make_csv_handler()
                    server_ref._meter.start(handler)
                    time.sleep(0.1)
                    server_ref._meter.stop()
                    energy = handler.get_data()
                    import os
                    os.unlink(tmp_file)
                    self._json({"energy_microjoules": energy, "method": "rapl_estimate"})
                except PermissionError:
                    self._error(503, "RAPL access denied - run with root/sudo for energy measurement")
                except Exception as e:
                    self._error(500, str(e))

            def _json(self, data: dict):
                import json
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps(data).encode())

            def _error(self, code: int, msg: str):
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(f'{{"error": "{msg}"}}'.encode())

        return EnergyHandler


def run_server(host: str = "127.0.0.1", port: int = 5001) -> EnergyServer:
    """Start and return the energy server."""
    server = EnergyServer(host, port)
    server.start()
    return server


if __name__ == "__main__":
    import signal
    import sys

    server = run_server()
    print(f"Energy server running on http://{server.host}:{server.port}")
    print("Endpoints:")
    print("  GET  /health          - Health check")
    print("  POST /measure/start   - Start energy measurement")
    print("  POST /measure/stop    - Stop and get energy reading")
    print("  GET  /measure         - Single-shot measurement (deprecated)")

    def shutdown(*_):
        print("\nShutting down...")
        server.stop()
        sys.exit(0)

    signal.signal(signal.SIGINT, shutdown)
    signal.signal(signal.SIGTERM, shutdown)
    server.thread.join()