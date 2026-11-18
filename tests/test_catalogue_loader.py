import json
import shutil

import pytest
import yaml

from backend.core.models import DiagnosticTest, Hypothesis
from backend.diagnosis.catalogue_loader import CatalogueError, DEFAULT_CATALOGUE_DIR, load_catalogues


@pytest.fixture
def catalogue_dir(tmp_path):
    for name in ('knowledge.yaml', 'tests.yaml', 'probabilities.json'):
        shutil.copy(DEFAULT_CATALOGUE_DIR / name, tmp_path / name)
    return tmp_path


def edit(directory, name, mutation):
    path = directory / name
    doc = json.loads(path.read_text()) if name.endswith('.json') else yaml.safe_load(path.read_text())
    mutation(doc)
    path.write_text(json.dumps(doc) if name.endswith('.json') else yaml.safe_dump(doc))


def test_load_existing_catalogues():
    result = load_catalogues(research_mode=True)
    assert len(result.hypotheses) == 15 and len(result.tests) == 20
    assert all(isinstance(h, Hypothesis) for h in result.hypotheses)
    assert all(isinstance(t, DiagnosticTest) for t in result.tests.values())
    assert result.tests['host_cpu_pressure'].expected_latency_seconds == .045
    assert result.tests['host_cpu_pressure'].expected_energy_joules == .02
    assert sum(result.initial_prior.values()) == pytest.approx(1)
    assert result.metadata['model']['status'] == 'synthetic_provisional_not_calibrated'
    assert result.metadata['model']['source_dataset'] is None
    assert result.metadata['knowledge']['version'] == '0.1.0'
    # Two tests are live-enabled (Docker-backed, verified in twin lab)
    live_tests = {'worker_runtime_state', 'worker_redis_connectivity'}
    for test_id, metadata in result.test_metadata.items():
        if test_id in live_tests:
            assert metadata['availability']['live'] is True
            assert metadata['execution']['binding_status'] == 'verified'
            assert metadata['execution']['live_binding'] is not None
        else:
            assert metadata['availability']['live'] is False
            assert metadata['execution']['live_binding'] is None
        assert metadata['evidence_group']
        assert metadata['source_basis']


def test_requires_explicit_research_mode():
    with pytest.raises(PermissionError, match='research_mode'):
        load_catalogues()


@pytest.mark.parametrize('filename', ['knowledge.yaml', 'tests.yaml', 'probabilities.json'])
def test_unsupported_schema(catalogue_dir, filename):
    edit(catalogue_dir, filename, lambda d: d.update(schema_version=2))
    with pytest.raises(CatalogueError, match='schema'):
        load_catalogues(catalogue_dir, research_mode=True)


def test_invalid_hypothesis_id(catalogue_dir):
    edit(catalogue_dir, 'knowledge.yaml', lambda d: d['hypotheses'].update({'bad id': {'description': 'bad'}}))
    with pytest.raises(CatalogueError, match='identifier'):
        load_catalogues(catalogue_dir, research_mode=True)


@pytest.mark.parametrize('mutation', [
    lambda d: d['relationships'][0].update(test_id='missing_test'),
    lambda d: d['relationships'][0]['hypothesis_outcomes'].update(missing_cause={'supports':'stopped','contradicts':'running'}),
    lambda d: d['relationships'][0]['hypothesis_outcomes']['redis_process_stopped'].update(supports='unknown_outcome'),
    lambda d: d['relationships'][0]['hypothesis_outcomes']['redis_process_stopped'].update(supports='running'),
])
def test_invalid_relationship(catalogue_dir, mutation):
    edit(catalogue_dir, 'knowledge.yaml', mutation)
    with pytest.raises(CatalogueError):
        load_catalogues(catalogue_dir, research_mode=True)


@pytest.mark.parametrize('mutation', [
    lambda d: d['likelihoods'].pop('host_cpu_pressure'),
    lambda d: d['likelihoods']['host_cpu_pressure'].pop('other_unknown'),
    lambda d: d['likelihoods']['host_cpu_pressure'].update(invalid_hypothesis={'normal':1}),
    lambda d: d['likelihoods']['host_cpu_pressure']['other_unknown'].pop('normal'),
    lambda d: d['likelihoods']['host_cpu_pressure']['other_unknown'].update(normal=.9),
    lambda d: d['likelihoods']['host_cpu_pressure']['other_unknown'].update(normal=float('nan')),
    lambda d: d['priors'].update(other_unknown=.8),
    lambda d: d['priors'].update(other_unknown=float('inf')),
    lambda d: d['priors'].update(other_unknown=True),
    lambda d: d['priors'].update(other_unknown=d['priors']['other_unknown'] + 1e-7),
])
def test_invalid_probability_model(catalogue_dir, mutation):
    edit(catalogue_dir, 'probabilities.json', mutation)
    with pytest.raises(CatalogueError):
        load_catalogues(catalogue_dir, research_mode=True)


@pytest.mark.parametrize('value', [-1, float('inf'), float('nan'), None, '2', True])
@pytest.mark.parametrize('field', ['energy_j', 'latency_ms'])
def test_invalid_cost(catalogue_dir, value, field):
    edit(catalogue_dir, 'tests.yaml', lambda d: d['tests']['host_cpu_pressure']['synthetic_cost_for_planner_simulation'].update({field:value}))
    with pytest.raises(CatalogueError):
        load_catalogues(catalogue_dir, research_mode=True)


@pytest.mark.parametrize('mutation', [
    lambda d: d['tests']['host_cpu_pressure'].update(outcomes=['normal', 'normal']),
    lambda d: d['tests']['host_cpu_pressure'].update(normal_outcome='missing'),
    lambda d: d['tests']['host_cpu_pressure']['availability'].update(live=True),
    lambda d: d['tests']['host_cpu_pressure']['availability'].update(live='false'),
    lambda d: d['tests']['host_cpu_pressure'].pop('description'),
])
def test_invalid_test_definition(catalogue_dir, mutation):
    edit(catalogue_dir, 'tests.yaml', mutation)
    with pytest.raises(CatalogueError):
        load_catalogues(catalogue_dir, research_mode=True)


def test_model_policy_blocks_research(catalogue_dir):
    edit(catalogue_dir, 'probabilities.json', lambda d: d['usage_policy'].update(simulation=False))
    with pytest.raises(PermissionError):
        load_catalogues(catalogue_dir, research_mode=True)


@pytest.mark.parametrize('name', ['knowledge.yaml', 'tests.yaml', 'probabilities.json'])
def test_missing_and_invalid_files(catalogue_dir, name):
    path = catalogue_dir / name
    path.write_text('[')
    with pytest.raises(CatalogueError):
        load_catalogues(catalogue_dir, research_mode=True)
    path.unlink()
    with pytest.raises(CatalogueError):
        load_catalogues(catalogue_dir, research_mode=True)


@pytest.mark.parametrize('name', ['knowledge.yaml', 'tests.yaml'])
def test_duplicate_yaml_keys(catalogue_dir, name):
    path = catalogue_dir / name
    path.write_text(path.read_text() + '\nschema_version: 1\n')
    with pytest.raises(CatalogueError, match='Duplicate'):
        load_catalogues(catalogue_dir, research_mode=True)


def test_duplicate_json_keys(catalogue_dir):
    path = catalogue_dir / 'probabilities.json'
    path.write_text(path.read_text().replace('"schema_version": 1', '"schema_version": 1, "schema_version": 1'))
    with pytest.raises(CatalogueError, match='Duplicate'):
        load_catalogues(catalogue_dir, research_mode=True)
