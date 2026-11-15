"""Normalize the existing Linux NetdataCollector CPU row, without provider I/O."""
from dataclasses import dataclass
from datetime import datetime, timezone
import math
from typing import Any

from backend.core.models import DiagnosticTest, ExecutionStatus, Observation, validate_cost

# Netdata proc.plugin removes guest from user/nice before charting. These are
# chart percentages, NOT raw /proc/stat counters. Iowait remains separate.
CPU_ACTIVITY_DIMENSIONS = ('user', 'system', 'nice', 'irq', 'softirq', 'steal', 'guest', 'guest_nice')


@dataclass(frozen=True)
class NormalizedObservation:
    observation: Observation
    metadata: dict[str, Any]


def _numeric(value):
    return (not isinstance(value, bool) and isinstance(value, (int, float))
            and math.isfinite(value))


def normalize_cpu_observation(snapshot: Any, test: DiagnosticTest,
                              test_metadata: dict[str, Any], target_identifier: str, *,
                              retrieved_at: datetime | None = None,
                              duration_seconds: float = 0.0) -> NormalizedObservation:
    """Map a collect() snapshot to host_cpu_pressure using catalogue thresholds.

    Missing sampling timestamps are UNAVAILABLE (freshness cannot be verified).
    For unsuccessful evidence Observation.timestamp records this processing attempt,
    never a fabricated sample time; metadata declares that distinction explicitly.
    Programmer/configuration errors raise ValueError; evidence errors yield statuses.
    """
    if test.identifier != 'host_cpu_pressure':
        raise ValueError('Only host_cpu_pressure normalization is supported')
    if set(test.outcomes) != {'normal', 'high', 'critical'}:
        raise ValueError('CPU outcome vocabulary mismatch')
    if not isinstance(target_identifier, str) or not target_identifier.strip():
        raise ValueError('A target identifier is required')
    now = retrieved_at if retrieved_at is not None else datetime.now(timezone.utc)
    if not isinstance(now, datetime) or now.tzinfo is None or now.utcoffset() is None:
        raise ValueError('retrieved_at must be timezone-aware')
    validate_cost(duration_seconds, 'duration')
    if not isinstance(test_metadata, dict):
        raise ValueError('CPU test metadata must be a mapping')
    config = test_metadata.get('normalization', {})
    if not isinstance(config, dict):
        raise ValueError('CPU normalization configuration must be a mapping')
    if (config.get('metric') != 'netdata.system.cpu.activity_percent'
            or config.get('units') != 'percentage'
            or config.get('dimensions') != list(CPU_ACTIVITY_DIMENSIONS)):
        raise ValueError('Unsupported or missing CPU metric configuration')
    thresholds = config.get('thresholds', {})
    if not isinstance(thresholds, dict):
        raise ValueError('CPU thresholds must be a mapping')
    high, critical = thresholds.get('high_min_percent'), thresholds.get('critical_min_percent')
    if not all(_numeric(v) for v in (high, critical)) or not 0 <= high < critical <= 100:
        raise ValueError('Invalid CPU thresholds')
    freshness = test_metadata.get('freshness_max_seconds')
    if not _numeric(freshness) or freshness < 0:
        raise ValueError('CPU normalization requires a finite freshness limit')
    window = config.get('requested_window')
    if window != {'after_seconds': -5, 'points': 1, 'group': 'agent_default'}:
        raise ValueError('CPU window does not match existing collector request')
    meta = {
        'source': 'netdata', 'target_identifier': target_identifier,
        'retrieved_at': now.isoformat(), 'measurement_timestamp': None,
        'observation_timestamp_role': 'processing_attempt',
        'cpu_utilization_percent': None, 'units': config['units'],
        'metric': config['metric'], 'dimensions': list(CPU_ACTIVITY_DIMENSIONS),
        'thresholds': dict(thresholds), 'requested_window': dict(window),
        'actual_window': None,
        'window_limitation': 'Collector retains one aggregate row, not actual window bounds or grouping metadata',
        'collector_errors': {}, 'reason': None,
    }
    sample_time = None

    def result(status, reason, outcome=None):
        meta['reason'] = reason
        if status == ExecutionStatus.COMPLETED:
            meta['observation_timestamp_role'] = 'measurement_sample'
        return NormalizedObservation(Observation(
            test_identifier=test.identifier, target_identifier=target_identifier,
            execution_status=status, observed_outcome=outcome,
            duration_seconds=duration_seconds, measured_energy_joules=None,
            timestamp=sample_time if status == ExecutionStatus.COMPLETED else now), meta)

    if not isinstance(snapshot, dict) or snapshot.get('source') != 'netdata':
        return result(ExecutionStatus.FAILED, 'malformed_collector_response')
    errors = snapshot.get('errors', {})
    if not isinstance(errors, dict):
        return result(ExecutionStatus.FAILED, 'malformed_collector_errors')
    meta['collector_errors'] = dict(errors)
    cpu = snapshot.get('cpu')
    if errors.get('cpu'):
        message = str(errors['cpu'])
        unavailable = 'No data available' in message or '404 Client Error' in message
        return result(ExecutionStatus.UNAVAILABLE if unavailable else ExecutionStatus.FAILED,
                      'cpu_unavailable' if unavailable else 'cpu_collector_failure')
    if cpu is None:
        return result(ExecutionStatus.UNAVAILABLE, 'missing_cpu_measurement')
    if not isinstance(cpu, dict):
        return result(ExecutionStatus.FAILED, 'unsupported_cpu_shape')
    allowed = set(CPU_ACTIVITY_DIMENSIONS) | {'time', 'iowait', 'idle'}
    if set(cpu) - allowed:
        return result(ExecutionStatus.FAILED, 'unsupported_cpu_dimensions')
    if any(k not in cpu or cpu[k] is None for k in CPU_ACTIVITY_DIMENSIONS):
        return result(ExecutionStatus.UNAVAILABLE, 'missing_cpu_dimensions')
    if any(not _numeric(v) or not 0 <= v <= 100 for k, v in cpu.items() if k != 'time'):
        return result(ExecutionStatus.FAILED, 'invalid_cpu_numeric_value')
    activity = math.fsum(cpu[k] for k in CPU_ACTIVITY_DIMENSIONS)
    if activity > 100 or math.fsum(v for k, v in cpu.items() if k != 'time') > 100.000001:
        return result(ExecutionStatus.FAILED, 'invalid_cpu_total')
    meta['cpu_utilization_percent'] = activity
    meta['iowait_percent'] = cpu.get('iowait')
    meta['raw_cpu'] = dict(cpu)
    stamp = cpu.get('time')
    if stamp is None:
        return result(ExecutionStatus.UNAVAILABLE, 'missing_measurement_timestamp')
    if not _numeric(stamp) or stamp <= 0:
        return result(ExecutionStatus.FAILED, 'invalid_measurement_timestamp')
    try:
        sample_time = datetime.fromtimestamp(stamp, timezone.utc)
    except (ValueError, OverflowError, OSError):
        return result(ExecutionStatus.FAILED, 'invalid_measurement_timestamp')
    meta['measurement_timestamp'] = sample_time.isoformat()
    age = (now - sample_time).total_seconds()
    meta['age_seconds'] = age
    if age < 0:
        return result(ExecutionStatus.UNAVAILABLE, 'future_measurement_timestamp')
    if age > freshness:
        return result(ExecutionStatus.UNAVAILABLE, 'stale_cpu_evidence')
    outcome = 'critical' if activity >= critical else 'high' if activity >= high else 'normal'
    return result(ExecutionStatus.COMPLETED, 'valid_cpu_measurement', outcome)
