"""Tests for the repair validator (guide §12)."""

from backend.twin.repair import validator
from backend.twin import catalogues


def test_repair_catalogue_loads():
    repairs = validator.load_repairs()
    assert "redis_process_stopped" in repairs
    assert "postgres_unavailable" in repairs
    assert "worker_process_stopped" in repairs
    assert "redis_network_unreachable" in repairs
    assert "connectivity_or_endpoint" in repairs
    assert "other_unknown" in repairs


def test_get_repair():
    repair = validator.get_repair("redis_process_stopped")
    assert repair["fault"] == "redis_process_stopped"
    assert repair["target_service"] == "redis"
    assert len(repair["actions"]) == 1
    assert repair["actions"][0]["method"] == "docker_start"


def test_unknown_repair_raises():
    try:
        validator.get_repair("nonexistent_fault")
        assert False, "Should have raised RepairError"
    except validator.RepairError as e:
        assert "nonexistent_fault" in str(e)


def test_repair_actions_structure():
    """Verify each repair has valid action structure."""
    repairs = validator.load_repairs()
    for fault_name, repair in repairs.items():
        assert "fault" in repair
        assert "target_service" in repair
        assert "actions" in repair
        assert len(repair["actions"]) > 0
        for action in repair["actions"]:
            assert "method" in action
            assert "command" in action
            assert "timeout_seconds" in action
            assert "verification" in action
            assert isinstance(action["command"], list)


def test_faults_yaml_has_matching_repairs():
    """Every fault in faults.yaml with applicable_to_v0_1 should have a repair."""
    faults = catalogues.load("faults")["faults"]
    repairs = validator.load_repairs()
    for fault_name, fault in faults.items():
        if fault.get("applicable_to_v0_1"):
            assert fault_name in repairs, f"Fault {fault_name} missing repair"


def test_validator_import_works():
    """Sanity check that validator module imports correctly."""
    from backend.twin.repair.validator import validate_repair, RepairError
    assert validate_repair is not None
    assert RepairError is not None