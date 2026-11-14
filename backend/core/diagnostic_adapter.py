
from backend.core.models import Observation, ExecutionStatus, DiagnosticTest


class DiagnosticAdapter:
    """
    Converts raw CollectorManager results into normalized Observations.

    It does not execute tests or update Bayesian beliefs.
    """

    def __init__(self, outcome_mappings=None):
        self.outcome_mappings = outcome_mappings or {}

    def adapt(self, test, execution_result, target):

        if not isinstance(test, DiagnosticTest):
            raise TypeError("test must be a DiagnosticTest")

        if not isinstance(execution_result, dict):
            raise TypeError("execution_result must be a dictionary")

        if not isinstance(target, dict):
            raise TypeError("target must be a dictionary")

        test_id = test.identifier
        target_id = self._get_target_identifier(target)

        status = execution_result.get("status")

        if status == ExecutionStatus.FAILED:
            return self._build_observation(
                test_id, target_id, ExecutionStatus.FAILED,
                execution_result
            )

        if status == ExecutionStatus.UNAVAILABLE:
            return self._build_observation(
                test_id, target_id, ExecutionStatus.UNAVAILABLE,
                execution_result
            )

        if status != ExecutionStatus.COMPLETED:
            return self._build_observation(
                test_id, target_id, ExecutionStatus.UNAVAILABLE,
                execution_result
            )

        raw_data = execution_result.get("data")

        if not isinstance(raw_data, dict):
            return self._build_observation(
                test_id, target_id, ExecutionStatus.UNAVAILABLE,
                execution_result
            )

        outcome = self._resolve_outcome(test_id, raw_data)

        if outcome not in test.outcomes:
            return self._build_observation(
                test_id, target_id, ExecutionStatus.UNAVAILABLE,
                execution_result
            )

        return self._build_observation(
            test_id, target_id, ExecutionStatus.COMPLETED,
            execution_result, outcome
        )

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
            "redis_error": signatures.get("redis_error_count", 0),
            "postgres_error": signatures.get("postgres_error_count", 0),
            "other_error": signatures.get("other_error_count", 0)
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

        for key in (
            "container_name",
            "container_id",
            "service",
            "host",
            "id"
        ):
            value = target.get(key)

            if isinstance(value, str) and value.strip():
                return value

        return "infrastructure"

    def _build_observation(
        self,
        test_id,
        target_id,
        status,
        execution_result,
        outcome=None
    ):

        duration = execution_result.get("duration_seconds", 0.0)
        energy = execution_result.get("measured_energy_joules")

        return Observation(
            test_identifier=test_id,
            target_identifier=target_id,
            execution_status=status,
            observed_outcome=outcome,
            duration_seconds=duration,
            measured_energy_joules=energy
        )
