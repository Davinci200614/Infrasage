"""Energy measurement client for InfraSage digital twin (Phase 12).

Communicates with the energy measurement server to record per-test energy.
Falls back to CPU-based estimation when RAPL is unavailable (no root).
"""

from __future__ import annotations

import math
import os
import platform
import time
from dataclasses import dataclass, field
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
        """Measure host baseline energy without lab running.
        
        Uses energy server for RAPL measurement (requires root).
        Returns not_measured if server unavailable — no fake estimates.
        """
        health = self.health_check()
        rapl_available = health.get("rapl_available", False)

        if rapl_available:
            # Use energy server start/stop for RAPL measurement
            try:
                if requests.post(f"{self.base_url}/measure/start", timeout=3).status_code == 200:
                    import time
                    time.sleep(duration_seconds)
                    resp = requests.post(f"{self.base_url}/measure/stop", timeout=5)
                    if resp.status_code == 200:
                        data = resp.json()
                        return BaselineEnergy(
                            energy_joules=data.get("energy_microjoules", 0) / 1_000_000,
                            energy_method="measured",
                            duration_seconds=data.get("elapsed_seconds", duration_seconds),
                            hardware_context=self._hardware_context,
                        )
            except Exception:
                pass

        # Server unavailable — honest "not measured"
        return BaselineEnergy(
            energy_joules=None,
            energy_method="not_measured",
            duration_seconds=duration_seconds,
            hardware_context=self._hardware_context,
        )

    def start_measurement(self) -> bool:
        """Start an energy measurement interval. Returns True if started."""
        self._measurement_started = time.monotonic()
        self._server_started = False
        try:
            resp = requests.post(f"{self.base_url}/measure/start", timeout=3)
            if resp.status_code == 200:
                self._server_started = True
                return True
            return False
        except Exception:
            return False

    def stop_measurement(self) -> EnergyReading:
        """Return optional hardware energy or a clearly labeled host CPU estimate."""
        if not hasattr(self, '_measurement_started') or self._measurement_started is None:
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
        """Convenience: measure energy over a fixed interval (start + sleep + stop)."""
        start = time.monotonic()
        if self.start_measurement():
            time.sleep(duration_seconds)
            return self.stop_measurement()
        else:
            # Server not available — honest "not measured"
            elapsed = time.monotonic() - start
            return EnergyReading(
                energy_joules=None,
                energy_method="not_measured",
                elapsed_seconds=elapsed,
                methods_attempted=["server_unavailable"],
                hardware_context=self._hardware_context,
            )


def get_energy_client() -> EnergyClient:
    """Factory for the energy client (uses env var for server URL if set)."""
    import os
    url = os.environ.get("INFRASAGE_ENERGY_SERVER", "http://127.0.0.1:5001")
    return EnergyClient(url)