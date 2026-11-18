import json
from pathlib import Path

import jsonschema
import pytest

from backend.api.investigate import InvestigationResult, LabTarget
from backend.twin.ground_truth import store
from backend.twin.runner import experiment

SCHEMA_PATH = Path(__file__).resolve().parents[3] / "experiments" / "schema" / "run_record.schema.json"

RUN_ID = "redis_process_stopped-0001"


@pytest.fixture
def run_env(tmp_path, monkeypatch):
    monkeypatch.setattr(experiment, "RUNS_DIR", tmp_path / "runs")
    monkeypatch.setattr(store, "DIR", tmp_path / "ground_truth")
    return tmp_path


def test_stub_diagnostic_returns_a_fixed_answer():
    result = experiment.stub_investigate(LabTarget())
    assert result.decision == "stub_cause"
    assert result.abstained is False


def test_full_run_writes_a_valid_record(lab_env, run_env):
    record = experiment.run("redis_process_stopped", seed=420, run_id=RUN_ID, target=lab_env.target, investigate=experiment.stub_investigate, warmup_seconds=0.0)
    assert record["lab_status"] == "valid"
    assert record["scenario"] == "redis_process_stopped"
    assert record["split"] == "calibration"
    assert record["ground_truth_label"] == "redis_process_stopped"
    assert record["scored"] == {"correct": False, "abstained": False}
    assert record["diagnosis"]["decision"] == "stub_cause"
    assert record["injection"]["status"] == "verified"
    assert record["injection"]["method"] == "docker_stop"
    assert (run_env / "runs" / f"{RUN_ID}.json").exists()


def test_label_timing_and_run_record_contents(lab_env, run_env, tmp_path, monkeypatch):
    seen: dict = {}

    def stub(target: LabTarget, energy_client=None) -> InvestigationResult:
        seen["label_exists_at_investigation"] = store.path_for(RUN_ID).exists()
        return experiment.stub_investigate(target)

    _ = experiment.run(
        "redis_process_stopped", seed=420, run_id=RUN_ID, target=lab_env.target, investigate=stub, warmup_seconds=0.0
    )
    assert seen["label_exists_at_investigation"] is True
    assert store.read(RUN_ID)["ground_truth_label"] == "redis_process_stopped"
    saved = json.loads((run_env / "runs" / f"{RUN_ID}.json").read_text())
    assert saved["ground_truth_label"] == "redis_process_stopped"
    assert saved["scored"]["correct"] is False


def test_invalid_baseline_stops_and_tears_down(lab_env, run_env):
    lab_env.psql_sees_votes = False
    record = experiment.run("redis_process_stopped", seed=420, run_id=RUN_ID, target=lab_env.target, warmup_seconds=0.0)
    assert record["lab_status"] == "invalid_baseline"
    assert not store.path_for(RUN_ID).exists()
    assert any("down" in call for call in lab_env.calls)


def test_injection_failure_stops_before_writing_the_label(lab_env, run_env):
    lab_env.stop_takes_effect = False
    record = experiment.run("redis_process_stopped", seed=420, run_id=RUN_ID, target=lab_env.target, warmup_seconds=0.0)
    assert record["lab_status"] == "injection_failed"
    assert not store.path_for(RUN_ID).exists()
    assert any("down" in call for call in lab_env.calls)


def test_investigation_error_marks_the_run_error_and_tears_down(lab_env, run_env):
    def boom(target: LabTarget) -> InvestigationResult:
        raise RuntimeError("engine down")

    record = experiment.run(
        "redis_process_stopped", seed=420, run_id=RUN_ID, target=lab_env.target, investigate=boom, warmup_seconds=0.0
    )
    assert record["lab_status"] == "error"
    assert any("down" in call for call in lab_env.calls)


def test_full_run_tears_the_stack_down_after_successful_injection(lab_env, run_env):
    experiment.run("redis_process_stopped", seed=420, run_id=RUN_ID, target=lab_env.target, warmup_seconds=0.0)
    assert any("down" in call for call in lab_env.calls)
    # Start is called with full container ID
    assert any(call[0] == "start" and "redis" in call[1] for call in lab_env.calls)


def test_run_record_validates_against_schema(lab_env, run_env):
    record = experiment.run("redis_process_stopped", seed=420, run_id=RUN_ID, target=lab_env.target, investigate=experiment.stub_investigate, warmup_seconds=0.0)
    schema = json.loads(SCHEMA_PATH.read_text())
    jsonschema.validate(record, schema)
