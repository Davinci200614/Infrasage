"""Energy measurement client for InfraSage digital twin (Phase 12).

Communicates with the energy measurement server to record per-test energy.
Falls back to CPU-based estimation when RAPL is unavailable (no root).
"""

from __future__ import annotations

import os
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

    def __init__(self, base_url: str = "http://127.0.0.1:5001"):
        self.base_url = base_url.rstrip("/")
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
        
        Note: CPU-based fallback blocks for `duration_seconds` to measure CPU%.
        For non-blocking baseline, use RAPL server (requires root).
        """
        health = self.health_check()
        rapl_available = health.get("rapl_available", False)

        if rapl_available:
            # Use energy server for RAPL measurement
            try:
                resp = requests.post(
                    f"{self.base_url}/measure/interval",
                    json={"duration_seconds": duration_seconds},
                    timeout=duration_seconds + 5,
                )
                if resp.status_code == 200:
                    data = resp.json()
                    return BaselineEnergy(
                        energy_joules=data.get("energy_joules"),
                        energy_method=data.get("energy_method", "measured"),
                        duration_seconds=data.get("duration_seconds", duration_seconds),
                        hardware_context=self._hardware_context,
                    )
            except Exception:
                pass

        # Fallback: CPU-based estimation (blocks for duration_seconds)
        cpu_percent = psutil.cpu_percent(interval=duration_seconds)
        # Rough estimate: assume ~65W TDP for typical server CPU (configurable via INFRASAGE_CPU_TDP)
        estimated_joules = _DEFAULT_CPU_TDP_WATTS * (cpu_percent / 100) * duration_seconds
        return BaselineEnergy(
            energy_joules=estimated_joules,
            energy_method="estimated",
            duration_seconds=duration_seconds,
            hardware_context={**self._hardware_context, "cpu_percent_baseline": cpu_percent},
        )

    def start_measurement(self) -> bool:
        """Start an energy measurement interval. Returns True if started."""
        try:
            resp = requests.post(f"{self.base_url}/measure/start", timeout=3)
            return resp.status_code == 200
        except Exception:
            return False

    def stop_measurement(self) -> EnergyReading:
        """Stop the active measurement and return the reading."""
        start_time = time.monotonic()
        methods = []

        try:
            resp = requests.post(f"{self.base_url}/measure/stop", timeout=5)
            elapsed = time.monotonic() - start_time
            if resp.status_code == 200:
                data = resp.json()
                return EnergyReading(
                    energy_joules=data.get("energy_microjoules", 0) / 1_000_000,
                    energy_method=data.get("method", "measured"),
                    elapsed_seconds=data.get("elapsed_seconds", elapsed),
                    methods_attempted=["pyJoules/RAPL"],
                    hardware_context=self._hardware_context,
                )
            methods.append(f"server_error_{resp.status_code}")
        except Exception as e:
            elapsed = time.monotonic() - start_time
            methods.append(f"request_failed: {e}")

        # Fallback: estimate from CPU usage during the interval
        # Note: this is a post-hoc estimate, less accurate
        try:
            cpu_percent = psutil.cpu_percent(interval=0.1)  # quick sample
            estimated = _DEFAULT_CPU_TDP_WATTS * (cpu_percent / 100) * elapsed
            methods.append(f"estimated_from_cpu ({cpu_percent}%)")
            return EnergyReading(
                energy_joules=estimated,
                energy_method="estimated",
                elapsed_seconds=elapsed,
                methods_attempted=methods,
                hardware_context={**self._hardware_context, "cpu_percent": cpu_percent},
            )
        except Exception as e:
            methods.append(f"psutil_failed: {e}")

        return EnergyReading(
            energy_joules=None,
            energy_method="not_measured",
            elapsed_seconds=time.monotonic() - start_time,
            methods_attempted=methods,
            hardware_context=self._hardware_context,
        )

    def measure_interval(self, duration_seconds: float) -> EnergyReading:
        """Convenience: measure energy over a fixed interval (start + sleep + stop)."""
        if self.start_measurement():
            time.sleep(duration_seconds)
            return self.stop_measurement()
        else:
            # Server not available, estimate directly
            start = time.monotonic()
            cpu_percent = psutil.cpu_percent(interval=duration_seconds)
            elapsed = time.monotonic() - start
            estimated = _DEFAULT_CPU_TDP_WATTS * (cpu_percent / 100) * elapsed
            return EnergyReading(
                energy_joules=estimated,
                energy_method="estimated",
                elapsed_seconds=elapsed,
                methods_attempted=["estimated_from_cpu"],
                hardware_context={**self._hardware_context, "cpu_percent": cpu_percent},
            )


def get_energy_client() -> EnergyClient:
    """Factory for the energy client (uses env var for server URL if set)."""
    import os
    url = os.environ.get("INFRASAGE_ENERGY_SERVER", "http://127.0.0.1:5001")
    return EnergyClient(url)