
import json
import shutil

import pytest
import yaml

from backend.diagnosis.catalogue_loader import (
    DEFAULT_CATALOGUE_DIR,
    CatalogueError,
    load_catalogues,
)


@pytest.fixture
def catalogue_dir(tmp_path):
    destination = tmp_path / "catalogues"
    shutil.copytree(DEFAULT_CATALOGUE_DIR, destination)
    return destination


def read_yaml(path):
    return yaml.safe_load(path.read_text())


def write_yaml(path, data):
    path.write_text(yaml.safe_dump(data, sort_keys=False))


def read_json(path):
    return json.loads(path.read_text())


def write_json(path, data):
    path.write_text(json.dumps(data, indent=2))


def test_default_catalogues_load_in_research_mode():
    result = load_catalogues(research_mode=True)

    assert result.research_mode is True
    assert result.hypotheses
    assert result.tests
    assert result.initial_prior
    assert result.metadata


def test_loading_without_research_mode_is_forbidden():
    with pytest.raises(PermissionError):
        load_catalogues(research_mode=False)


@pytest.mark.parametrize("value", ["true", 1, None])
def test_research_mode_must_be_boolean(value):
    with pytest.raises(CatalogueError):
        load_catalogues(research_mode=value)


def test_unknown_directory_raises_catalogue_error(tmp_path):
    with pytest.raises(CatalogueError):
        load_catalogues(tmp_path / "missing", research_mode=True)


def test_duplicate_yaml_keys_are_rejected(catalogue_dir):
    path = catalogue_dir / "knowledge.yaml"
    path.write_text(path.read_text() + "\nschema_version: 1\n")

    with pytest.raises(CatalogueError, match="Duplicate key"):
        load_catalogues(catalogue_dir, research_mode=True)


def test_duplicate_json_keys_are_rejected(catalogue_dir):
    path = catalogue_dir / "probabilities.json"
    path.write_text('{"schema_version": 1, "schema_version": 1}')

    with pytest.raises(CatalogueError, match="Duplicate key"):
        load_catalogues(catalogue_dir, research_mode=True)


def test_invalid_schema_version_is_rejected(catalogue_dir):
    path = catalogue_dir / "knowledge.yaml"
    data = read_yaml(path)
    data["schema_version"] = 99
    write_yaml(path, data)

    with pytest.raises(CatalogueError, match="schema version"):
        load_catalogues(catalogue_dir, research_mode=True)


def test_invalid_prior_hypothesis_ids_are_rejected(catalogue_dir):
    path = catalogue_dir / "probabilities.json"
    data = read_json(path)
    data["priors"]["unknown_hypothesis"] = 0.1
    write_json(path, data)

    with pytest.raises(CatalogueError, match="Prior hypothesis IDs"):
        load_catalogues(catalogue_dir, research_mode=True)


def test_invalid_prior_sum_is_rejected(catalogue_dir):
    path = catalogue_dir / "probabilities.json"
    data = read_json(path)

    first_key = next(iter(data["priors"]))
    data["priors"][first_key] = 0.9
    write_json(path, data)

    with pytest.raises(CatalogueError):
        load_catalogues(catalogue_dir, research_mode=True)


def test_invalid_test_identifier_is_rejected(catalogue_dir):
    tests_path = catalogue_dir / "tests.yaml"
    probabilities_path = catalogue_dir / "probabilities.json"

    tests_data = read_yaml(tests_path)
    probabilities_data = read_json(probabilities_path)

    first_key = next(iter(tests_data["tests"]))
    invalid_key = "Invalid-Identifier"

    entry = tests_data["tests"].pop(first_key)
    tests_data["tests"][invalid_key] = entry

    likelihoods = probabilities_data["likelihoods"]
    likelihoods[invalid_key] = likelihoods.pop(first_key)

    write_yaml(tests_path, tests_data)
    write_json(probabilities_path, probabilities_data)

    with pytest.raises(CatalogueError, match="Invalid symbolic identifier"):
        load_catalogues(catalogue_dir, research_mode=True)


def test_live_availability_requires_verified_binding(catalogue_dir):
    path = catalogue_dir / "tests.yaml"
    data = read_yaml(path)
    first_test = next(iter(data["tests"].values()))

    first_test["availability"]["live"] = True
    first_test["execution"]["binding_status"] = "assumed_unverified"
    first_test["execution"]["live_binding"] = None

    write_yaml(path, data)

    with pytest.raises(CatalogueError, match="live availability"):
        load_catalogues(catalogue_dir, research_mode=True)


def test_invalid_probability_table_is_rejected(catalogue_dir):
    path = catalogue_dir / "probabilities.json"
    data = read_json(path)

    first_test = next(iter(data["likelihoods"].values()))
    first_hypothesis = next(iter(first_test))
    first_outcome = next(iter(first_test[first_hypothesis]))

    first_test[first_hypothesis][first_outcome] = 10
    write_json(path, data)

    with pytest.raises(CatalogueError):
        load_catalogues(catalogue_dir, research_mode=True)


def test_model_policy_can_forbid_simulation(catalogue_dir):
    path = catalogue_dir / "probabilities.json"
    data = read_json(path)
    data["usage_policy"]["simulation"] = False
    write_json(path, data)

    with pytest.raises(PermissionError, match="forbids"):
        load_catalogues(catalogue_dir, research_mode=True)


def test_invalid_availability_flag_is_rejected(catalogue_dir):
    path = catalogue_dir / "tests.yaml"
    data = read_yaml(path)
    first_test = next(iter(data["tests"].values()))
    first_test["availability"]["simulation"] = "yes"
    write_yaml(path, data)

    with pytest.raises(CatalogueError, match="availability flags"):
        load_catalogues(catalogue_dir, research_mode=True)


def test_loaded_catalogues_keep_execution_metadata(catalogue_dir):
    result = load_catalogues(catalogue_dir, research_mode=True)

    assert result.test_metadata
    assert result.metadata["knowledge"]
    assert result.metadata["tests"]
    assert result.metadata["model"]
