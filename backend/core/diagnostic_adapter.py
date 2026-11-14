"""DiagnosticAdapter from teammate commit 838ab10, with strict identity/provenance checks."""
from copy import deepcopy
from datetime import datetime, timezone
import math
from backend.collectors.docker import valid_container_id, valid_container_name
from backend.core.collector_manager import CollectorManager
from backend.processing.normalizer import NormalizedObservation
from backend.core.models import Observation, ExecutionStatus, DiagnosticTest


def finite_nonnegative(value):
    try:
        return not isinstance(value, bool) and isinstance(value, (int, float)) and math.isfinite(value) and value >= 0
    except OverflowError:
        return False


class DiagnosticAdapter:
    """
    Converts raw CollectorManager results into normalized Observations.

    It does not execute tests or update Bayesian beliefs.
    """

    def __init__(self, outcome_mappings=None, *, freshness_max_seconds=15):
        self.outcome_mappings = {"redis_runtime_state": {
            "type": "field", "field": "status",
            "values": {"running": "running", "exited": "stopped", "restarting": "restarting"}}}
        if outcome_mappings is not None:
            self.outcome_mappings.update(outcome_mappings)
        if not finite_nonnegative(freshness_max_seconds) or freshness_max_seconds == 0:
            raise ValueError("A positive finite freshness limit is required")
        self.freshness_max_seconds = freshness_max_seconds

    def adapt(self, test, execution_result, target):
        """Backwards-compatible Observation return; use adapt_with_metadata for audit context."""
        return self.adapt_with_metadata(test, execution_result, target).observation

    # --------------------------------------------------
    # OUTCOME RESOLUTION
    # --------------------------------------------------

    def _resolve_outcome(self, test_id, raw_data):

        mapping = self.outcome_mappings.get(test_id)

        if not isinstance(mapping, dict):
            return None

        mapping_type = mapping.get("type", "field")

        if mapping_type == "field":
            return self._resolve_field(raw_data, mapping)

        if mapping_type == "restart_activity":
            return self._resolve_restart_activity(raw_data, mapping)

        if mapping_type == "worker_error_signature":
            return self._resolve_worker_errors(raw_data, mapping)

        return None

    # --------------------------------------------------
    # SIMPLE FIELD MAPPING
    # --------------------------------------------------

    def _resolve_field(self, raw_data, mapping):

        field = mapping.get("field")
        values = mapping.get("values", {})

        if not isinstance(field, str) or not isinstance(values, dict):
            return None

        raw_value = self._get_nested_value(raw_data, field)

        if raw_value is None:
            return None

        return values.get(str(raw_value))

    # --------------------------------------------------
    # DOCKER RESTART ACTIVITY
    # --------------------------------------------------

    def _resolve_restart_activity(self, raw_data, mapping):

        count_field = mapping.get("count_field", "restart_count")
        repeated_threshold = mapping.get("repeated_threshold", 2)

        if (
            not isinstance(repeated_threshold, int)
            or isinstance(repeated_threshold, bool)
            or repeated_threshold < 2
        ):
            return None

        count = self._get_nested_value(raw_data, count_field)

        if (
            not isinstance(count, int)
            or isinstance(count, bool)
            or count < 0
        ):
            return None

        if count == 0:
            return "none"

        if count >= repeated_threshold:
            return "repeated"

        return "recent"

    # --------------------------------------------------
    # DOCKER WORKER LOGS
    # --------------------------------------------------

    def _resolve_worker_errors(self, raw_data, mapping):

        signatures_field = mapping.get(
            "signatures_field",
            "error_signatures"
        )

        signatures = self._get_nested_value(
            raw_data,
            signatures_field
        )

        if not isinstance(signatures, dict):
            return None

        categories = {
            "redis_error": signatures.get("redis_error_count"),
            "postgres_error": signatures.get("postgres_error_count"),
            "other_error": signatures.get("other_error_count")
        }

        # Validate counters
        for count in categories.values():
            if (
                not isinstance(count, int)
                or isinstance(count, bool)
                or count < 0
            ):
                return None

        detected = [
            category
            for category, count in categories.items()
            if count > 0
        ]

        if not detected:
            return "none"

        # Avoid choosing arbitrarily if several categories are present.
        if len(detected) > 1:
            return None

        return detected[0]

    # --------------------------------------------------
    # GENERIC HELPERS
    # --------------------------------------------------

    def _get_nested_value(self, data, path):

        if not isinstance(path, str):
            return None

        value = data

        for key in path.split("."):
            if not isinstance(value, dict):
                return None

            value = value.get(key)

            if value is None:
                return None

        return value

    def _get_target_identifier(self, target):
        if not isinstance(target, dict) or not valid_container_id(target.get("container_id")):
            raise ValueError("Canonical full container identity required")
        return "docker:" + target["container_id"]

    def _build_observation(
        self,
        test_id,
        target_id,
        status,
        execution_result,
        outcome=None,
        *,
        timestamp=None
    ):

        if timestamp is None or timestamp.tzinfo is None or timestamp.utcoffset() is None:
            raise ValueError("An explicit aware evidence or processing timestamp is required")
        duration = execution_result.get("duration_seconds", 0.0)
        energy = execution_result.get("measured_energy_joules")

        return Observation(
            test_identifier=test_id,
            target_identifier=target_id,
            execution_status=status,
            observed_outcome=outcome,
            duration_seconds=duration,
            measured_energy_joules=energy,
            timestamp=timestamp
        )

    def adapt_with_metadata(self, test, execution_result, target, *, retrieved_at=None):
        if not isinstance(test, DiagnosticTest):
            raise TypeError("test must be a DiagnosticTest")
        if not isinstance(execution_result, dict):
            raise TypeError("execution_result must be a dictionary")
        if not isinstance(target, dict) or not any(target.get(k) for k in ("container_id", "container_name")):
            raise ValueError("An explicit target identity is required")
        if ((target.get("container_id") is not None and not valid_container_id(target["container_id"]))
                or (target.get("container_name") is not None and not valid_container_name(target["container_name"]))):
            raise ValueError("Ambiguous or malformed target identity")
        now = retrieved_at if retrieved_at is not None else datetime.now(timezone.utc)
        if not isinstance(now, datetime) or now.tzinfo is None or now.utcoffset() is None:
            raise ValueError("retrieved_at must be timezone-aware")
        target_id = ("docker:" + target["container_id"] if target.get("container_id")
                     else "docker-name:" + target["container_name"])
        meta = {"execution_result": deepcopy(execution_result), "source": execution_result.get("source"),
                "retrieved_at": now.isoformat(), "measurement_timestamp": execution_result.get("observed_at"),
                "observation_timestamp_role": "processing_attempt", "identity_verified": False}

        def finish(status, reason, outcome=None, stamp=None):
            meta["reason"] = reason
            duration = execution_result.get("duration_seconds", 0.0)
            energy = execution_result.get("measured_energy_joules")
            if not finite_nonnegative(duration):
                duration = 0.0
            if energy is not None and not finite_nonnegative(energy):
                energy = None
            measurements = {"duration_seconds": duration, "measured_energy_joules": energy}
            obs = self._build_observation(test.identifier, target_id, status, measurements, outcome,
                                          timestamp=stamp if stamp is not None else now)
            return NormalizedObservation(obs, meta)

        if execution_result.get("test_id") != test.identifier:
            return finish(ExecutionStatus.FAILED, "execution_test_identifier_mismatch")
        status = execution_result.get("status")
        if status in (ExecutionStatus.FAILED, ExecutionStatus.UNAVAILABLE):
            return finish(ExecutionStatus(status), "provider_execution_unsuccessful")
        if status != ExecutionStatus.COMPLETED:
            return finish(ExecutionStatus.UNAVAILABLE, "unsupported_execution_status")
        if execution_result.get("error") or execution_result.get("errors"):
            return finish(ExecutionStatus.FAILED, "completed_result_contains_errors")
        if execution_result.get("source") != "docker":
            return finish(ExecutionStatus.FAILED, "unexpected_provider")
        identity = execution_result.get("target")
        raw_data = execution_result.get("data")
        if not isinstance(identity, dict) or not isinstance(raw_data, dict):
            return finish(ExecutionStatus.FAILED, "missing_provider_identity_or_data")
        cid, name = identity.get("container_id"), identity.get("container_name")
        if (not valid_container_id(cid) or not valid_container_name(name)
                or (target.get("container_id") is not None and target["container_id"] != cid)
                or (target.get("container_name") is not None and target["container_name"] != name)
                or raw_data.get("id") != cid or raw_data.get("name") != name
                or execution_result.get("target_identifier") != "docker:" + cid):
            return finish(ExecutionStatus.FAILED, "execution_target_identifier_mismatch")
        target_id = self._get_target_identifier(identity)
        meta["identity_verified"] = True
        binding = execution_result.get("binding")
        if (not CollectorManager._valid_binding(binding)
                or binding["container_id"] != cid or binding["container_name"] != name
                or execution_result.get("docker_endpoint") != binding["docker_endpoint"]):
            return finish(ExecutionStatus.FAILED, "missing_or_mismatched_verified_binding")
        try:
            stamp = datetime.fromisoformat(execution_result["observed_at"])
            provider_retrieved = datetime.fromisoformat(execution_result["retrieved_at"])
            verified = datetime.fromisoformat(binding["verified_at"])
            if any(s.tzinfo is None or s.utcoffset() is None for s in (stamp, provider_retrieved, verified)):
                raise ValueError("naive timestamp")
        except (KeyError, TypeError, ValueError):
            return finish(ExecutionStatus.UNAVAILABLE, "missing_or_invalid_evidence_timestamp")
        age = (now - stamp).total_seconds()
        binding_age = (now - verified).total_seconds()
        if (age < 0 or age > self.freshness_max_seconds
                or binding_age < 0 or binding_age > binding["freshness_max_seconds"]
                or not verified <= stamp <= provider_retrieved <= now):
            return finish(ExecutionStatus.UNAVAILABLE, "stale_or_inconsistent_evidence_timestamps")
        if (not finite_nonnegative(execution_result.get("duration_seconds"))
                or (execution_result.get("measured_energy_joules") is not None
                    and not finite_nonnegative(execution_result["measured_energy_joules"]))):
            return finish(ExecutionStatus.FAILED, "invalid_execution_measurements")
        outcome = self._resolve_outcome(test.identifier, raw_data)
        if outcome not in test.outcomes:
            return finish(ExecutionStatus.UNAVAILABLE, "unsupported_or_missing_outcome")
        meta["observation_timestamp_role"] = "measurement_sample"
        return finish(ExecutionStatus.COMPLETED, "validated_runtime_observation", outcome, stamp)

