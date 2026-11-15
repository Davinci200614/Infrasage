"""Energy measurement client for InfraSage digital twin (Phase 12).

Communicates with the energy measurement server to record per-test energy.
Reports not_measured when RAPL is unavailable. Rough CPU estimates require
explicit allow_estimates=True and are never reported as measured.
"""

from __future__ import annotations

import os
import math
import platform
import subprocess
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

import psutil
import requests

# Default TDP for CPU-based energy estimation (watts). Override via INFRASAGE_CPU_TDP.
_DEFAULT_CPU_TDP_WATTS = float(os.environ.get("INFRASAGE_CPU_TDP", "65"))


@dataclass(frozen=True)
class EnergyReading:
    """Result of an energy measurement interval."""
    energy_joules: float | None
    energy_method: str  # "measured" | "estimated" | "not_measured"
    elapsed_seconds: float
    methods_attempted: list[str]
    hardware_context: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class BaselineEnergy:
    """Host baseline energy measurement (no lab running)."""
    energy_joules: float | None
    energy_method: str
    duration_seconds: float
    hardware_context: dict[str, Any]


class EnergyClient:
    """Client for the energy measurement HTTP server."""

    def __init__(self, base_url: str = "http://127.0.0.1:5001", *, allow_estimates: bool = False):
        self.base_url = base_url.rstrip("/")
        self.allow_estimates = allow_estimates
        self._hardware_context = self._collect_hardware_context()
        self._measurement_started = None
        self._server_started = False

    def _collect_hardware_context(self) -> dict[str, Any]:
        """Collect static hardware/OS context for energy reporting."""
        ctx = {
            "cpu_model": platform.processor() or "unknown",
            "cpu_count": psutil.cpu_count(logical=False),
            "cpu_count_logical": psutil.cpu_count(logical=True),
            "os": platform.system(),
            "os_release": platform.release(),
            "python_version": platform.python_version(),
        }
        # Try to get CPU frequency
        try:
            freq = psutil.cpu_freq()
            if freq:
                ctx["cpu_freq_max_mhz"] = freq.max
                ctx["cpu_freq_min_mhz"] = freq.min
        except Exception:
            pass
        # Memory info
        try:
            mem = psutil.virtual_memory()
            ctx["total_memory_gb"] = round(mem.total / (1024**3), 1)
        except Exception:
            pass
        return ctx

    def health_check(self) -> dict[str, Any]:
        """Check if energy server is reachable and RAPL available."""
        try:
            resp = requests.get(f"{self.base_url}/health", timeout=2)
            if resp.status_code == 200:
                return resp.json()
        except Exception as e:
            return {"status": "unreachable", "error": str(e), "rapl_available": False}
        return {"status": "error", "rapl_available": False}

    def measure_baseline(self, duration_seconds: float = 10.0) -> BaselineEnergy:
        """Measure host baseline energy without lab running."""
        reading = self.measure_interval(duration_seconds)
        return BaselineEnergy(reading.energy_joules, reading.energy_method,
                              reading.elapsed_seconds, reading.hardware_context)

    def start_measurement(self) -> bool:
        """Start an energy measurement interval. Returns True if started."""
        self._measurement_started = time.monotonic()
        self._server_started = False
        try:
            resp = requests.post(f"{self.base_url}/measure/start", timeout=1)
            self._server_started = resp.status_code == 200
        except Exception:
            pass
        return self._server_started

    def stop_measurement(self) -> EnergyReading:
        """Return optional hardware energy or a clearly labeled host CPU estimate."""
        if self._measurement_started is None:
            return EnergyReading(None, "not_measured", 0.0, ["no_active_interval"], self._hardware_context)
        started, self._measurement_started = self._measurement_started, None
        elapsed = max(0.0, time.monotonic() - started)
        methods = []
        if self._server_started:
            self._server_started = False
            try:
                resp = requests.post(f"{self.base_url}/measure/stop", timeout=1)
                data = resp.json() if resp.status_code == 200 else {}
                value = data.get("energy_microjoules")
                if (data.get("method") == "measured" and isinstance(value, (float, int))
                        and not isinstance(value, bool) and math.isfinite(value) and value >= 0):
                    return EnergyReading(value / 1_000_000, "measured", elapsed,
                                         ["pyJoules/RAPL"], {**self._hardware_context,
                                         "scope": "host_cpu_packages"})
                methods.append("invalid_or_unavailable_hardware_reading")
            except Exception as error:
                methods.append(f"request_failed: {error}")
        if not self.allow_estimates:
            return EnergyReading(None, "not_measured", elapsed,
                                 methods + ["hardware_unavailable"], self._hardware_context)
        try:
            # A post-hoc CPU sample is only a rough estimate of interval cost.
            cpu_percent = psutil.cpu_percent(interval=0.1)
            estimated = _DEFAULT_CPU_TDP_WATTS * (cpu_percent / 100) * elapsed
            if not math.isfinite(estimated) or estimated < 0:
                raise ValueError("Invalid CPU estimate")
            return EnergyReading(estimated, "estimated", elapsed, methods + ["estimated_from_cpu"],
                                 {**self._hardware_context, "cpu_percent": cpu_percent,
                                  "scope": "host_cpu_estimate", "tdp_watts": _DEFAULT_CPU_TDP_WATTS})
        except Exception as error:
            return EnergyReading(None, "not_measured", elapsed, methods + [str(error)], self._hardware_context)

    def measure_interval(self, duration_seconds: float) -> EnergyReading:
        self.start_measurement()
        time.sleep(duration_seconds)
        return self.stop_measurement()


def get_energy_client() -> EnergyClient:
    """Factory for the energy client (uses env var for server URL if set)."""
    import os
    url = os.environ.get("INFRASAGE_ENERGY_SERVER", "http://127.0.0.1:5001")
    return EnergyClient(url)