from backend.twin.workload import generator


def test_run_is_deterministic_and_end_to_end(lab_env):
    records = generator.run(0.35, target=lab_env.target, rate=10, seed=7)
    assert 1 <= len(records) <= 5
    assert [r["voter_id"] for r in records] == [f"w7-{i}" for i in range(len(records))]
    assert [r["vote"] for r in records] == [generator.VOTES[i % 2] for i in range(len(records))]
    assert all(r["ok"] for r in records)
    assert lab_env.votes == {r["voter_id"]: r["vote"] for r in records}


def test_failed_submission_is_recorded_not_raised(lab_env):
    records = generator.run(0.2, target="http://127.0.0.1:1", rate=10, seed=1)
    assert records
    assert records[0]["ok"] is False
    assert records[0]["error"]
