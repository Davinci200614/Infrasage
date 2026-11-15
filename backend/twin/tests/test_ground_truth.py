import pytest

from backend.twin.ground_truth import store


@pytest.fixture
def store_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "DIR", tmp_path / "ground_truth")
    return store.DIR


def test_label_is_written_once_owner_only(store_dir):
    path = store.write("run-001", label="redis_process_stopped", fault_version=1, injected_at="2026-01-01T00:00:00Z")
    assert path.parent == store_dir
    assert oct(path.stat().st_mode & 0o777) == "0o600"
    assert store.read("run-001")["ground_truth_label"] == "redis_process_stopped"


def test_second_write_for_the_same_run_is_refused(store_dir):
    store.write("run-001", label="redis_process_stopped", fault_version=1, injected_at="2026-01-01T00:00:00Z")
    with pytest.raises(FileExistsError):
        store.write("run-001", label="postgres_unavailable", fault_version=1, injected_at="2026-01-01T00:00:01Z")


def test_run_id_cannot_escape_the_store(store_dir):
    with pytest.raises(ValueError):
        store.path_for("../runs/run-001")
    with pytest.raises(ValueError):
        store.path_for("")
