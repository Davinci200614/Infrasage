
from backend.core.collection_orchestrator import CollectionOrchestrator


class CollectorManager:
    """
    Interface between the diagnostic system and infrastructure collectors.

    Responsibilities:
    - Collect the initial infrastructure snapshot.
    - Resolve diagnostic tests from the catalogue.
    - Enforce live execution permissions.
    - Dispatch supported operations to CollectionOrchestrator.
    - Return raw execution results.

    It does not:
    - Select the next diagnostic test.
    - Interpret diagnostic outcomes.
    - Calculate Bayesian probabilities.
    - Update beliefs.
    """

    def __init__(self, catalogue, collection_orchestrator=None):
        self.catalogue = catalogue

        self.collection_orchestrator = (
            collection_orchestrator
            if collection_orchestrator is not None
            else CollectionOrchestrator()
        )

    # --------------------------------------------------
    # INITIAL SNAPSHOT
    # --------------------------------------------------

    def collect_snapshot(self, target=None):
        """
        Collect the initial infrastructure state.

        The existing CollectionOrchestrator remains responsible
        for coordinating Netdata and Docker collectors.
        """
        try:
            return self.collection_orchestrator.collect_initial()

        except Exception as error:
            return {
                "netdata": None,
                "docker": None,
                "errors": {
                    "collection": str(error)
                }
            }

    # --------------------------------------------------
    # TEST IDENTIFIER
    # --------------------------------------------------

    def _get_test_id(self, test):

        if isinstance(test, str):
            return test

        if isinstance(test, dict):
            return test.get("identifier") or test.get("id")

        if hasattr(test, "identifier"):
            return test.identifier

        return None

    # --------------------------------------------------
    # CATALOGUE RESOLUTION
    # --------------------------------------------------

    def _get_test_definition(self, test_id):

        if not isinstance(self.catalogue, dict):
            return None

        tests = self.catalogue.get("tests", {})

        if not isinstance(tests, dict):
            return None

        return tests.get(test_id)

    # --------------------------------------------------
    # TARGETED DIAGNOSTIC TEST
    # --------------------------------------------------

    def execute_test(self, test, target):

        test_id = self._get_test_id(test)

        if not isinstance(test_id, str) or not test_id.strip():
            return self._result(
                test_id,
                "unavailable",
                error="Invalid test identifier"
            )

        if not isinstance(target, dict):
            return self._result(
                test_id,
                "unavailable",
                error="Target must be a dictionary"
            )

        # 1. Resolve test definition
        test_definition = self._get_test_definition(test_id)

        if test_definition is None:
            return self._result(
                test_id,
                "unavailable",
                error="Test not found in catalogue"
            )

        # 2. Read execution configuration
        execution = test_definition.get("execution") or {}
        availability = test_definition.get("availability") or {}

        if not isinstance(execution, dict):
            execution = {}

        if not isinstance(availability, dict):
            availability = {}

        # Support both nested and root-level catalogue fields.
        # This does not bypass any execution restrictions.
        binding_status = execution.get(
            "binding_status",
            test_definition.get("binding_status")
        )

        live_binding = execution.get(
            "live_binding",
            test_definition.get("live_binding")
        )

        operation = execution.get(
            "logical_operation",
            test_definition.get(
                "op",
                test_definition.get("logical_operation")
            )
        )

        provider = test_definition.get("provider")

        # 3. Enforce live execution policy
        if availability.get("live") is not True:
            return self._result(
                test_id,
                "unavailable",
                error="Live execution is disabled for this test"
            )

        if binding_status != "verified":
            return self._result(
                test_id,
                "unavailable",
                error="Test binding has not been verified"
            )

        if not live_binding:
            return self._result(
                test_id,
                "unavailable",
                error="No live binding configured"
            )

        # 4. Validate provider
        if not isinstance(provider, str):
            return self._result(
                test_id,
                "unavailable",
                error="No valid provider configured"
            )

        provider = provider.strip().lower()

        # 5. Dispatch
        try:
            if provider == "docker":
                return self._execute_docker_test(
                    test_id,
                    operation,
                    target
                )

            return self._result(
                test_id,
                "unavailable",
                error=f"Unsupported provider: {provider}"
            )

        except Exception as error:
            return self._result(
                test_id,
                "failed",
                error=str(error)
            )

    # --------------------------------------------------
    # DOCKER OPERATIONS
    # --------------------------------------------------

    def _execute_docker_test(self, test_id, operation, target):

        if operation == "container_process_state":
            return self._execute_runtime_state(
                test_id,
                target
            )

        if operation == "container_restart_history":
            return self._execute_restart_history(
                test_id,
                target
            )

        if operation == "read_bounded_worker_logs":
            return self._execute_worker_logs(
                test_id,
                target
            )

        return self._result(
            test_id,
            "unavailable",
            error=f"Unsupported Docker operation: {operation}"
        )

    # --------------------------------------------------
    # CONTAINER IDENTIFIER
    # --------------------------------------------------

    def _get_container_id(self, target):

        container_id = (
            target.get("container_id")
            or target.get("container_name")
        )

        if not isinstance(container_id, str):
            return None

        container_id = container_id.strip()

        return container_id or None

    # --------------------------------------------------
    # CONTAINER RUNTIME STATE
    # --------------------------------------------------

    def _execute_runtime_state(self, test_id, target):

        container_id = self._get_container_id(target)

        if container_id is None:
            return self._result(
                test_id,
                "unavailable",
                error="Container identifier is required"
            )

        # Reuse the initial collection pipeline.
        snapshot = self.collect_snapshot(target)

        errors = snapshot.get("errors", {})

        if errors.get("docker"):
            return self._result(
                test_id,
                "failed",
                error=errors["docker"]
            )

        containers = snapshot.get("docker")

        if containers is None:
            return self._result(
                test_id,
                "failed",
                error="Docker snapshot is unavailable"
            )

        matching = []

        for container in containers:
            container_id_value = container.get("id", "")
            container_name = container.get("name", "")

            if (
                container_id_value == container_id
                or container_name == container_id
                or (
                    isinstance(container_id_value, str)
                    and container_id_value.startswith(container_id)
                )
            ):
                matching.append(container)

        if not matching:
            return self._result(
                test_id,
                "unavailable",
                error=f"Container not found: {container_id}"
            )

        return self._result(
            test_id,
            "completed",
            data=matching[0]
        )

    # --------------------------------------------------
    # CONTAINER RESTART HISTORY
    # --------------------------------------------------

    def _execute_restart_history(self, test_id, target):

        container_id = self._get_container_id(target)

        if container_id is None:
            return self._result(
                test_id,
                "unavailable",
                error="Container identifier is required"
            )

        result = self.collection_orchestrator.collect_on_demand(
            "events",
            container_id=container_id,
            lookback_seconds=target.get(
                "lookback_seconds",
                60
            ),
            event_actions=["die", "start"]
        )

        return self._convert_collection_result(
            test_id,
            result
        )

    # --------------------------------------------------
    # WORKER LOG INSPECTION
    # --------------------------------------------------

    def _execute_worker_logs(self, test_id, target):

        container_id = self._get_container_id(target)

        if container_id is None:
            return self._result(
                test_id,
                "unavailable",
                error="Container identifier is required"
            )

        result = self.collection_orchestrator.collect_on_demand(
            "logs",
            container_id=container_id,
            tail=target.get("tail", 100),
            since=target.get("since"),
            until=target.get("until")
        )

        return self._convert_collection_result(
            test_id,
            result
        )

    # --------------------------------------------------
    # COLLECTION RESULT NORMALIZATION
    # --------------------------------------------------

    def _convert_collection_result(self, test_id, result):

        if not isinstance(result, dict):
            return self._result(
                test_id,
                "failed",
                error="Invalid collection result"
            )

        errors = result.get("errors") or {}
        data = result.get("data")

        if errors:
            return self._result(
                test_id,
                "failed",
                error=errors,
                data=data
            )

        if data is None:
            return self._result(
                test_id,
                "failed",
                error="Collection returned no data"
            )

        if isinstance(data, dict) and data.get("success") is False:
            return self._result(
                test_id,
                "failed",
                error=data.get(
                    "error",
                    "Collection failed"
                ),
                data=data
            )

        return self._result(
            test_id,
            "completed",
            data=data
        )

    # --------------------------------------------------
    # STANDARD RESULT
    # --------------------------------------------------

    def _result(self, test_id, status, data=None, error=None):

        result = {
            "test_id": test_id,
            "status": status,
            "data": data
        }

        if error is not None:
            result["error"] = error

        return result
