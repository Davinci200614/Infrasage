"""CollectorManager from teammate commit 838ab10, hardened for Phase 2A."""
from copy import deepcopy
from datetime import datetime, timezone
import math
import time
from backend.collectors.docker import DockerCollector, valid_container_id, valid_container_name
from backend.collectors.tcp_probe import CONFIG_KEYS, valid_probe_config
from backend.diagnosis.catalogue_loader import LoadedCatalogues

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

    def __init__(self, catalogue, collection_orchestrator=None, *, docker_collector=None):
        # Adapt the Phase 1 loader without changing its API or its documents.
        self.catalogue = catalogue
        self.collection_orchestrator = collection_orchestrator
        self.docker_collector = docker_collector
        self._owns_docker = False
        self._verified_definitions = {}

    # --------------------------------------------------
    # INITIAL SNAPSHOT
    # --------------------------------------------------

    def collect_snapshot(self, target=None):
        try:
            if self.collection_orchestrator is None:
                self.collection_orchestrator = CollectionOrchestrator()
            return self.collection_orchestrator.collect_initial()
        except Exception as error:
            return {"netdata": None, "docker": None, "errors": {"collection": str(error)}}

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
        if test_id in self._verified_definitions:
            return self._verified_definitions[test_id]
        if isinstance(self.catalogue, LoadedCatalogues):
            return self.catalogue.test_metadata.get(test_id)
        if isinstance(self.catalogue, dict) and isinstance(self.catalogue.get("tests"), dict):
            return self.catalogue["tests"].get(test_id)
        return None

    # --------------------------------------------------
    # TARGETED DIAGNOSTIC TEST
    # --------------------------------------------------

    def execute_test(self, test, target):
        test_id = self._get_test_id(test)
        if not isinstance(test_id, str) or not test_id.strip():
            return self._result(test_id, "unavailable", error="Invalid test identifier")
        definition = self._get_test_definition(test_id)
        if not isinstance(definition, dict):
            return self._result(test_id, "unavailable", error="Test not found or malformed catalogue definition")
        execution = definition.get("execution")
        availability = definition.get("availability")
        if not isinstance(execution, dict) or not isinstance(availability, dict):
            return self._result(test_id, "unavailable", error="Malformed execution metadata")
        if availability.get("live") is not True:
            return self._result(test_id, "unavailable", error="Live execution is disabled for this test")
        if execution.get("binding_status") != "verified":
            return self._result(test_id, "unavailable", error="Test binding has not been verified")
        binding = execution.get("live_binding")
        if not self._valid_binding(binding):
            return self._result(test_id, "unavailable", error="Malformed or missing verified live binding")
        operations = {"redis_runtime_state": "container_process_state",
                      "worker_redis_connectivity": "worker_to_redis_tcp"}
        if (definition.get("provider") != "docker"
                or test_id not in operations
                or execution.get("logical_operation") != operations[test_id]
                or binding["operation"] != operations[test_id]):
            return self._result(test_id, "unavailable", error="Unsupported provider, test or operation")
        if not self._target_matches(target, binding):
            return self._result(test_id, "unavailable", error="Missing, ambiguous or mismatched target identity")
        if test_id == "worker_redis_connectivity" and any(
                key in target and target[key] != binding[key] for key in CONFIG_KEYS):
            return self._result(test_id, "unavailable", error="Probe endpoint or configuration differs from verified binding")
        age = (datetime.now(timezone.utc) - datetime.fromisoformat(binding["verified_at"])).total_seconds()
        if age < 0 or age > binding["freshness_max_seconds"]:
            return self._result(test_id, "unavailable", error="Runtime binding verification expired; verify again")
        try:
            result = self._execute_docker_test(test_id, execution["logical_operation"], target)
            if result.get("status") == "completed":
                actual = result.get("target", {})
                if (actual.get("container_id") != binding["container_id"]
                        or actual.get("container_name") != binding["container_name"]
                        or result.get("docker_endpoint") != binding["docker_endpoint"]):
                    return {**result, "status": "failed", "binding": deepcopy(binding),
                            "error": "Docker identity changed since binding verification"}
                if test_id == "worker_redis_connectivity" and not self._connectivity_result_matches(result, binding):
                    return {**result, "status": "failed", "binding": deepcopy(binding),
                            "error": "Probe result differs from verified endpoint or namespace"}
            result["binding"] = deepcopy(binding)
            return result
        except Exception as error:
            return self._result(test_id, "failed", error=str(error))

    # --------------------------------------------------
    # DOCKER OPERATIONS
    # --------------------------------------------------

    def _execute_docker_test(self, test_id, operation, target):

        if operation == "worker_to_redis_tcp":
            binding = self._get_test_definition(test_id)["execution"]["live_binding"]
            return self._execute_connectivity(test_id, target, {key: binding[key] for key in CONFIG_KEYS})

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
        # Targeted inspect only: no Netdata requests, stats or whole-host snapshot.
        result = self._docker().inspect_runtime(
            container_id=target.get("container_id"), container_name=target.get("container_name"))
        return {**result, "test_id": test_id}

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

    def _docker(self, *, timeout=None):
        if self.docker_collector is None:
            if self.collection_orchestrator is not None:
                self.docker_collector = self.collection_orchestrator.docker_collector
            else:
                self.docker_collector = DockerCollector() if timeout is None else DockerCollector(timeout=timeout)
                self._owns_docker = True
        return self.docker_collector


    @staticmethod
    def _valid_binding(binding):
        if not isinstance(binding, dict):
            return False
        if (binding.get("provider") != "docker" or binding.get("operation") not in ("container_process_state", "worker_to_redis_tcp")
                or not valid_container_id(binding.get("container_id"))
                or not valid_container_name(binding.get("container_name"))
                or not isinstance(binding.get("docker_endpoint"), str) or not binding["docker_endpoint"]):
            return False
        if binding["operation"] == "worker_to_redis_tcp" and not valid_probe_config(binding):
            return False
        limit = binding.get("freshness_max_seconds")
        if isinstance(limit, bool) or not isinstance(limit, (int, float)) or not 0 < limit <= 15:
            return False
        if not math.isfinite(limit):
            return False
        try:
            stamp = datetime.fromisoformat(binding["verified_at"])
            return stamp.tzinfo is not None and stamp.utcoffset() is not None
        except (KeyError, TypeError, ValueError):
            return False


    @staticmethod
    def _target_matches(target, identity):
        if not isinstance(target, dict):
            return False
        cid, name = target.get("container_id"), target.get("container_name")
        if cid is None and name is None:
            return False
        return ((cid is None or (valid_container_id(cid) and cid == identity.get("container_id")))
                and (name is None or (valid_container_name(name) and name == identity.get("container_name"))))


    def verify_runtime_binding(self, test, target):
        """Read-only inspect and activate one process-local research binding.

        No catalogue file is changed. Verification itself never samples a fault
        outcome or updates beliefs. The following execution performs a new inspect.
        """
        test_id = self._get_test_id(test)
        if not isinstance(test_id, str) or not test_id.strip():
            return self._result(None, "unavailable", error="Invalid test identifier")
        if not isinstance(self.catalogue, LoadedCatalogues) or not self.catalogue.research_mode:
            return self._result(test_id, "unavailable", error="Verification requires explicit loaded research catalogues")
        self._verified_definitions.pop(test_id, None)
        original = self.catalogue.test_metadata.get(test_id)
        if (test_id != "redis_runtime_state" or not isinstance(original, dict)
                or not isinstance(original.get("execution"), dict)
                or not isinstance(original.get("availability"), dict)
                or original["execution"].get("logical_operation") != "container_process_state"
                or original["availability"].get("simulation") is not True):
            return self._result(test_id, "unavailable", error="Only the Redis runtime operation can be verified in Phase 2A")
        if not isinstance(target, dict) or not any(target.get(k) for k in ("container_id", "container_name")):
            return self._result(test_id, "unavailable", error="Explicit Docker target required")
        try:
            result = self._execute_runtime_state(test_id, target)
            if result.get("status") != "completed":
                return result
            identity = result.get("target", {})
            if not self._target_matches(target, identity):
                return self._result(test_id, "failed", error="Provider target identity mismatch")
            if result.get("data", {}).get("status") not in ("running", "exited", "restarting"):
                return self._result(test_id, "unavailable", error="Docker state has no validated runtime outcome mapping")
            binding = {"provider": "docker", "operation": "container_process_state",
                       **identity, "verified_at": result.get("observed_at"),
                       "docker_endpoint": result.get("docker_endpoint"), "freshness_max_seconds": 15}
            if not self._valid_binding(binding):
                return self._result(test_id, "failed", error="Provider returned malformed binding evidence")
            age = (datetime.now(timezone.utc) - datetime.fromisoformat(binding["verified_at"])).total_seconds()
            if age < 0 or age > binding["freshness_max_seconds"]:
                return {**result, "status": "unavailable", "error": "Stale or future binding evidence"}
            if (result.get("source") != "docker" or result.get("error") or result.get("errors")
                    or result["data"].get("id") != identity.get("container_id")
                    or result["data"].get("name") != identity.get("container_name")
                    or result.get("target_identifier") != "docker:" + identity["container_id"]):
                return {**result, "status": "failed", "error": "Inconsistent provider binding evidence"}
            definition = deepcopy(original)
            definition["provider"] = "docker"
            definition["availability"]["live"] = True
            definition["execution"].update(binding_status="verified", live_binding=binding)
            self._verified_definitions[test_id] = definition
            return {**result, "binding": deepcopy(binding), "verification_scope": "process-local research only"}
        except Exception as error:
            return self._result(test_id, "failed", error=str(error))


    def close(self):
        if self._owns_docker and self.docker_collector is not None:
            self.docker_collector.client.close()
            self.docker_collector = None
            self._owns_docker = False


    def _execute_connectivity(self, test_id, target, config):
        start = time.monotonic()
        deadline = start + config['execution_deadline']
        try:
            # Bound initial Docker version negotiation as well as probe requests.
            provider = self._docker(timeout=min(2, config['execution_deadline']))
            result = provider.probe_worker_redis(
                container_id=target.get('container_id'), container_name=target.get('container_name'),
                **config, _deadline_at=deadline)
            return {**result, 'test_id': test_id}
        except Exception as error:
            return {**self._result(test_id, 'failed', error=str(error)),
                    'source': 'docker', 'probe_config': dict(config),
                    'duration_seconds': time.monotonic() - start}

    @staticmethod
    def _connectivity_result_matches(result, binding):
        data, payload = result.get('data'), result.get('probe_result')
        if not isinstance(data, dict) or not isinstance(payload, dict):
            return False
        endpoint = {'host': binding['redis_host'], 'port': binding['redis_port']}
        return (result.get('probe_config') == {key: binding[key] for key in CONFIG_KEYS}
                and result.get('namespace') == 'container:' + binding['container_id']
                and result.get('helper_image') == binding['helper_image']
                and result.get('helper_removed') is True
                and data.get('endpoint') == endpoint and payload.get('endpoint') == endpoint
                and payload.get('protocol') == 'infrasage-tcp-v1'
                and payload.get('request_id') == result.get('request_id')
                and payload.get('worker_id') == binding['container_id']
                and payload.get('status') == 'completed' and not payload.get('error')
                and payload.get('outcome') == data.get('outcome')
                and data.get('outcome') in ('connected', 'refused', 'timeout')
                and payload.get('observed_at') == result.get('observed_at'))

    def verify_connectivity_binding(self, test, target, *, redis_host, redis_port,
                                    connection_timeout=1.0, execution_deadline=8.0, helper_image):
        """Verify the fixed helper operation before enabling a scoped lab binding.

        Verification is itself a real TCP probe; execution performs another probe.
        DNS/launch/deadline/cleanup failures never activate the binding.
        """
        test_id = self._get_test_id(test)
        if test_id != 'worker_redis_connectivity':
            return self._result(None, 'unavailable', error='Only worker_redis_connectivity can use this verification')
        self._verified_definitions.pop(test_id, None)
        if not isinstance(self.catalogue, LoadedCatalogues) or not self.catalogue.research_mode:
            return self._result(test_id, 'unavailable', error='Explicit research catalogues required')
        original = self.catalogue.test_metadata.get(test_id)
        if (not isinstance(original, dict) or not isinstance(original.get('execution'), dict)
                or not isinstance(original.get('availability'), dict)
                or original['execution'].get('logical_operation') != 'worker_to_redis_tcp'
                or original['availability'].get('simulation') is not True):
            return self._result(test_id, 'unavailable', error='Unsupported catalogue operation or research availability')
        config = dict(redis_host=redis_host, redis_port=redis_port, connection_timeout=connection_timeout,
                      execution_deadline=execution_deadline, helper_image=helper_image)
        if (not valid_probe_config(config) or not self._target_matches(target, target)
                or any(key in target and target[key] != config[key] for key in CONFIG_KEYS)):
            return self._result(test_id, 'unavailable', error='Invalid explicit worker, Redis endpoint or probe configuration')
        result = self._execute_connectivity(test_id, target, config)
        if result.get('status') != 'completed':
            return result
        identity = result.get('target', {})
        binding = {'provider': 'docker', 'operation': 'worker_to_redis_tcp', **identity, **config,
                   'docker_endpoint': result.get('docker_endpoint'), 'verified_at': result.get('observed_at'),
                   'freshness_max_seconds': 15}
        if (not self._valid_binding(binding) or not self._target_matches(target, binding)
                or not self._connectivity_result_matches(result, binding)
                or result.get('source') != 'docker' or result.get('error') or result.get('errors')
                or result.get('target_identifier') != 'docker:' + binding.get('container_id', '')
                or result.get('data', {}).get('id') != binding.get('container_id')
                or result.get('data', {}).get('name') != binding.get('container_name')):
            return {**result, 'status': 'failed', 'error': 'Malformed probe verification evidence'}
        age = (datetime.now(timezone.utc) - datetime.fromisoformat(binding['verified_at'])).total_seconds()
        if not 0 <= age <= 15:
            return {**result, 'status': 'unavailable', 'error': 'Stale probe verification evidence'}
        definition = deepcopy(original)
        definition['provider'] = 'docker'
        definition['availability']['live'] = True
        definition['execution'].update(binding_status='verified', live_binding=binding)
        self._verified_definitions[test_id] = definition
        return {**result, 'binding': deepcopy(binding), 'verification_scope': 'process-local research only'}
