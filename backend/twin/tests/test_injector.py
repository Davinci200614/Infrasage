from backend.twin.injector import injector


def test_apply_stops_the_service_and_verifies(lab_env):
    result = injector.apply("redis_process_stopped")
    assert result["method"] == "docker_stop"
    assert result["status"] == "verified"
    assert result["target_service"] == "redis"
    assert lab_env.running["redis"] is False
    assert ("stop", "cid-redis") in lab_env.calls


def test_apply_reports_failed_when_the_stop_does_not_take_effect(lab_env):
    lab_env.stop_takes_effect = False
    result = injector.apply("postgres_unavailable")
    assert result["status"] == "failed"
    assert lab_env.running["db"] is True


def test_revert_restarts_the_service_and_verifies(lab_env):
    injector.apply("postgres_unavailable")
    result = injector.revert("postgres_unavailable")
    assert result["method"] == "docker_start"
    assert result["status"] == "verified"
    assert lab_env.running["db"] is True


def test_unverified_target_is_refused(lab_env):
    lab_env.running.pop("redis")  # no container carries the lab's labels
    try:
        injector.apply("redis_process_stopped")
    except RuntimeError as exc:
        assert "expected one infrasage-lab/redis container" in str(exc)
    else:
        raise AssertionError("injector must refuse a container without the lab label")


def test_unknown_fault_is_refused():
    try:
        injector.apply("not_in_the_catalogue")
    except KeyError:
        pass
    else:
        raise AssertionError("ad hoc faults outside the catalogue must not run")
