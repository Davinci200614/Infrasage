from backend.twin.runner import lab


def test_service_states_parses_ndjson_and_array(lab_env):
    lab_env.ps_format = "ndjson"
    assert set(lab.service_states()) == set(lab.SERVICES)
    lab_env.ps_format = "array"
    assert set(lab.service_states()) == set(lab.SERVICES)


def test_residue_is_empty_when_clean_and_lists_leftovers(lab_env):
    assert lab.residue() == {"containers": [], "volumes": [], "networks": []}
    lab_env.leftovers["volumes"] = ["vol-1"]
    lab_env.leftovers["networks"] = ["net-1"]
    assert lab.residue() == {"containers": [], "volumes": ["vol-1"], "networks": ["net-1"]}


def test_baseline_passes_when_the_vote_reaches_the_database(lab_env):
    result = lab.check_baseline(target=lab_env.target, timeout=2)
    assert result["ok"], result
    assert lab_env.votes[result["voter_id"]] == "a"


def test_baseline_fails_when_a_service_is_down(lab_env):
    lab_env.running["worker"] = False
    result = lab.check_baseline(target=lab_env.target, timeout=2)
    assert not result["ok"]
    assert "worker" in result["reason"]


def test_baseline_fails_when_the_vote_never_lands(lab_env):
    lab_env.psql_sees_votes = False
    result = lab.check_baseline(target=lab_env.target, timeout=0.2)
    assert not result["ok"]
    assert "never reached the database" in result["reason"]


def test_baseline_fails_when_the_frontend_is_unreachable(lab_env):
    result = lab.check_baseline(target="http://127.0.0.1:1", timeout=2)
    assert not result["ok"]
    assert "vote submission failed" in result["reason"]


def test_reset_tears_down_before_bringing_up(lab_env):
    lab.reset()
    compose_calls = [call for call in lab_env.calls if call[0] == "compose"]
    assert any("down" in call for call in compose_calls)
    assert any("up" in call for call in compose_calls)
    assert compose_calls.index(next(c for c in compose_calls if "down" in c)) < compose_calls.index(
        next(c for c in compose_calls if "up" in c)
    )
