#!/usr/bin/env python3
"""
Energy measurement MCP server for InfraSage.
Exposes pyJoules/Scaphandre measurements via HTTP API.
"""

from __future__ import annotations

import json
import subprocess
import sys
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any


class EnergyHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/health":
            self._respond(200, {"status": "ok"})
        elif self.path == "/measure":
            result = self._measure_energy()
            self._respond(200, result)
        elif self.path == "/measure/baseline":
            result = self._measure_baseline()
            self._respond(200, result)
        else:
            self._respond(404, {"error": "Not found"})

    def do_POST(self):
        if self.path == "/measure/interval":
            content_length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(content_length)
            params = json.loads(body) if body else {}
            duration = params.get("duration_seconds", 10)
            result = self._measure_interval(duration)
            self._respond(200, result)
        else:
            self._respond(404, {"error": "Not found"})

    def _measure_energy(self) -> dict[str, Any]:
        """Measure current energy consumption using available tools."""
        results = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "methods": [],
            "energy_joules": None,
            "energy_method": "not_measured",
        }

        # Try pyJoules (Intel RAPL) - requires root for RAPL file access
        try:
            from pyJoules.energy_meter import EnergyMeter
            from pyJoules.device.rapl_device import RaplDevice

            # Check if we can read RAPL files (need root for /sys/class/powercap/intel-rapl/)
            import os
            rapl_file = '/sys/class/powercap/intel-rapl/intel-rapl:0/energy_uj'
            if os.access(rapl_file, os.R_OK):
                device = RaplDevice()
                device.configure()
                meter = EnergyMeter(devices=[device])
                meter.start()
                import time
                time.sleep(1)
                meter.stop()
                trace = meter.get_trace()
                if trace and trace.energy:
                    pkg_energy = sum(e for e in trace.energy if "PACKAGE" in str(e))
                    if pkg_energy:
                        results["energy_joules"] = pkg_energy / 1_000_000  # µJ to J
                        results["energy_method"] = "measured"
                        results["methods"].append("pyJoules/RAPL")
            else:
                results["methods"].append("pyJoules: RAPL files not readable (need root)")
        except Exception as e:
            results["methods"].append(f"pyJoules: {e}")

        # Try Scaphandre (if available)
        try:
            result = subprocess.run(
                ["scaphandre", "json", "-n", "1", "--no-cpu-idle"],
                capture_output=True,
                text=True,
                timeout=5,
            )
            if result.returncode == 0:
                data = json.loads(result.stdout)
                if data and "host" in data[0]:
                    host = data[0]["host"]
                    if "consumption" in host:
                        results["energy_joules"] = host["consumption"] / 1_000_000
                        results["energy_method"] = "measured"
                        results["methods"].append("Scaphandre")
        except Exception as e:
            results["methods"].append(f"Scaphandre: {e}")

        # Fallback: estimate from CPU usage
        if results["energy_method"] == "not_measured":
            try:
                import psutil
                cpu_percent = psutil.cpu_percent(interval=1)
                # Very rough estimate: ~65W TDP * (cpu%/100) * 1s
                estimated = 65 * (cpu_percent / 100)
                results["energy_joules"] = estimated
                results["energy_method"] = "estimated"
                results["methods"].append(f"estimated_from_cpu ({cpu_percent}%)")
            except Exception as e:
                results["methods"].append(f"psutil: {e}")

        return results

    def _measure_baseline(self) -> dict[str, Any]:
        """Measure baseline energy without lab running."""
        return self._measure_energy()

    def _measure_interval(self, duration: float) -> dict[str, Any]:
        """Measure energy over a specific interval."""
        results = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "duration_seconds": duration,
            "methods": [],
            "energy_joules": None,
            "energy_method": "not_measured",
        }

        try:
            from pyJoules.energy_meter import EnergyMeter
            from pyJoules.device.rapl_device import RaplDevice

            import os
            rapl_file = '/sys/class/powercap/intel-rapl/intel-rapl:0/energy_uj'
            if os.access(rapl_file, os.R_OK):
                device = RaplDevice()
                device.configure()
                meter = EnergyMeter(devices=[device])
                meter.start()
                import time
                time.sleep(duration)
                meter.stop()
                trace = meter.get_trace()
                if trace and trace.energy:
                    pkg_energy = sum(e for e in trace.energy if "PACKAGE" in str(e))
                    if pkg_energy:
                        results["energy_joules"] = pkg_energy / 1_000_000
                        results["energy_method"] = "measured"
                        results["methods"].append("pyJoules/RAPL")
            else:
                results["methods"].append("pyJoules: RAPL files not readable (need root)")
        except Exception as e:
            results["methods"].append(f"pyJoules: {e}")

        # Fallback: estimate from CPU usage over interval
        if results["energy_method"] == "not_measured":
            try:
                import psutil
                cpu_percent = psutil.cpu_percent(interval=duration)
                estimated = 65 * (cpu_percent / 100) * duration
                results["energy_joules"] = estimated
                results["energy_method"] = "estimated"
                results["methods"].append(f"estimated_from_cpu ({cpu_percent}%)")
            except Exception as e:
                results["methods"].append(f"psutil: {e}")

        return results

    def _respond(self, status: int, data: dict):
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps(data, indent=2).encode())


def main():
    port = 5001
    server = HTTPServer(("127.0.0.1", port), EnergyHandler)
    print(f"Energy MCP server running on http://127.0.0.1:{port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down...")
        server.shutdown()


if __name__ == "__main__":
    main()