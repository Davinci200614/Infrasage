
from backend.core.collection_orchestrator import CollectionOrchestrator


class CollectorManager:
    """
    Interface between the diagnostic system and infrastructure collectors.

    Responsibilities:
    - Collect the initial infrastructure snapshot.
    - Resolve diagnostic tests from LoadedCatalogues.
    - Enforce live execution permissions.
    - Resolve containers using Docker Compose service labels.
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
        """Collect the initial infrastructure state."""
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
        """Retrieve execution metadata from LoadedCatalogues."""

        metadata = getattr(self.catalogue, "test_metadata", None)

        if isinstance(metadata, dict):
            return metadata.get(test_id)

        # Backward compatibility with the old dictionary interface
        if isinstance(self.catalogue, dict):
            tests = self.catalogue.get("tests", {})

            if isinstance(tests, dict):
                return tests.get(test_id)

        return None

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

        # Phase 1: research mode does not authorize live execution.
        if getattr(self.catalogue, "research_mode", False):
            return self._result(
                test_id,
                "unavailable",
                error="Live diagnostic execution is disabled in Phase 1"
            )

        # 1. Resolve test metadata
        test_metadata = self._get_test_definition(test_id)

        if test_metadata is None:
            return self._result(
                test_id,
                "unavailable",
                error="Test not found in catalogue metadata"
            )

        # 2. Read execution configuration
        execution = test_metadata.get("execution") or {}
        availability = test_metadata.get("availability") or {}

        if not isinstance(execution, dict):
            execution = {}

        if not isinstance(availability, dict):
            availability = {}

        binding_status = execution.get("binding_status")
        live_binding = execution.get("live_binding")
        operation = execution.get("logical_operation")
        provider = test_metadata.get("provider")

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

        # 5. Build an effective target without modifying the caller's target.
        effective_target = dict(target)

        target_selector = test_metadata.get("target_selector")

        if isinstance(target_selector, dict):
            effective_target.setdefault(
                "target_selector",
                target_selector
            )

        # 6. Dispatch
        try:
            if provider == "docker":
                return self._execute_docker_test(
                    test_id,
                    operation,
                    effective_target
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
    # COMPOSE SERVICE RESOLUTION
    # --------------------------------------------------

    def _get_compose_service(self, target):
        """
        Extract the Docker Compose service from the diagnostic target.

        Supported target formats:
        - {"component": "redis"}
        - {"service": "redis"}
        - {"target_selector": {"component": "redis"}}
        """

        selector = target.get("target_selector") or {}

        if not isinstance(selector, dict):
            selector = {}

        component = (
            target.get("component")
            or target.get("service")
            or selector.get("component")
        )

        if not isinstance(component, str):
            return None

        component = component.strip()

        if not component:
            return None

        # The Compose service is named "db" in lab/compose.yaml.
        aliases = {
            "postgres": "db"
        }

        return aliases.get(component, component)

    # --------------------------------------------------
    # CONTAINER RESOLUTION
    # --------------------------------------------------

    def _resolve_container(self, target):
        """
        Resolve a container using either:
        - Explicit container ID/name.
        - Docker Compose service label.
        """

        container_id = self._get_container_id(target)
        compose_service = self._get_compose_service(target)

        if container_id is None and compose_service is None:
            return None, "Container identifier is required"

        snapshot = self.collect_snapshot(target)

        if not isinstance(snapshot, dict):
            return None, "Invalid Docker snapshot"

        errors = snapshot.get("errors") or {}

        if isinstance(errors, dict):
            if errors.get("docker"):
                return None, errors["docker"]

            if errors.get("collection"):
                return None, errors["collection"]

        containers = snapshot.get("docker")

        if not isinstance(containers, list):
            return None, "Docker snapshot is unavailable"

        matching = []

        for container in containers:

            if not isinstance(container, dict):
                continue

            container_id_value = container.get("id", "")
            container_name = container.get("name", "")
            service_name = container.get("compose_service")

            if container_id is not None:
                matches = (
                    container_id_value == container_id
                    or container_name == container_id
                    or (
                        isinstance(container_id_value, str)
                        and container_id_value.startswith(container_id)
                    )
                )
            else:
                matches = service_name == compose_service

            if matches:
                matching.append(container)

        if not matching:
            identifier = container_id or compose_service

            return None, f"Container not found: {identifier}"

        if len(matching) > 1:
            identifier = container_id or compose_service

            return None, f"Multiple containers match: {identifier}"

        return matching[0], None

    # --------------------------------------------------
    # RESOLUTION ERROR STATUS
    # --------------------------------------------------

    def _get_resolution_error_status(self, error):
        """
        Collection errors are failures.
        Missing or ambiguous containers are unavailable results.
        """

        if error in (
            "Docker snapshot is unavailable",
            "Invalid Docker snapshot"
        ):
            return "failed"

        # Errors returned by Docker/CollectionOrchestrator
        # are strings here, unlike the previous dictionary behavior.
        if error and error not in (
            "Container identifier is required",
        ) and not str(error).startswith(
            (
                "Container not found:",
                "Multiple containers match:"
            )
        ):
            return "failed"

        return "unavailable"

    # --------------------------------------------------
    # CONTAINER RUNTIME STATE
    # --------------------------------------------------

    def _execute_runtime_state(self, test_id, target):

        container, error = self._resolve_container(target)

        if container is None:
            return self._result(
                test_id,
                self._get_resolution_error_status(error),
                error=error
            )

        return self._result(
            test_id,
            "completed",
            data=container
        )

    # --------------------------------------------------
    # CONTAINER RESTART HISTORY
    # --------------------------------------------------

    def _execute_restart_history(self, test_id, target):

        container, error = self._resolve_container(target)

        if container is None:
            return self._result(
                test_id,
                self._get_resolution_error_status(error),
                error=error
            )

        container_id = container.get("id") or container.get("name")

        if not container_id:
            return self._result(
                test_id,
                "unavailable",
                error="Resolved container has no identifier"
            )

        result = self.collection_orchestrator.collect_on_demand(
            "events",
            container_id=container_id,
            lookback_seconds=target.get("lookback_seconds", 60),
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

        container, error = self._resolve_container(target)

        if container is None:
            return self._result(
                test_id,
                self._get_resolution_error_status(error),
                error=error
            )

        container_id = container.get("id") or container.get("name")

        if not container_id:
            return self._result(
                test_id,
                "unavailable",
                error="Resolved container has no identifier"
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
                error=data.get("error", "Collection failed"),
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
