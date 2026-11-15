"""Strict catalogue-to-engine boundary. Phase 1 supports explicit research use only."""
from dataclasses import dataclass
import json
from pathlib import Path
import re
from typing import Any

import yaml

from backend.core.models import DiagnosticTest, Hypothesis, validate_cost, validate_distribution

DEFAULT_CATALOGUE_DIR = Path(__file__).resolve().parent / 'catalogues'
_IDENTIFIER = re.compile(r'[a-z][a-z0-9_]*\Z')


class CatalogueError(ValueError):
    """A catalogue could not be parsed or violates the supported schema."""


class _UniqueSafeLoader(yaml.SafeLoader):
    """Safe YAML loader that does not silently overwrite duplicate identifiers."""


def _unique_pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise CatalogueError(f'Duplicate key: {key!r}')
        result[key] = value
    return result


def _yaml_mapping(loader, node):
    loader.flatten_mapping(node)
    return _unique_pairs((loader.construct_object(k), loader.construct_object(v))
                         for k, v in node.value)


_UniqueSafeLoader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _yaml_mapping)


def _mapping(value, label):
    if not isinstance(value, dict) or not value:
        raise CatalogueError(f'{label} must be a nonempty mapping')
    return value


def _identifier(value):
    if not isinstance(value, str) or not _IDENTIFIER.fullmatch(value):
        raise CatalogueError(f'Invalid symbolic identifier: {value!r}')
    return value


def _number(value, label):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise CatalogueError(f'{label} must be numeric')
    validate_cost(value, label)
    return value


def _text(value, label):
    if not isinstance(value, str) or not value.strip():
        raise CatalogueError(f'{label} must be a nonempty string')
    return value


@dataclass(frozen=True)
class LoadedCatalogues:
    hypotheses: tuple[Hypothesis, ...]
    initial_prior: dict[str, float]
    tests: dict[str, DiagnosticTest]
    test_metadata: dict[str, dict[str, Any]]
    metadata: dict[str, Any]
    research_mode: bool


def load_catalogues(directory: str | Path = DEFAULT_CATALOGUE_DIR, *,
                    research_mode: bool = False) -> LoadedCatalogues:
    """Load all three files without enabling any diagnostic execution.

    Requires explicit ``research_mode=True``. Production inference is intentionally
    unsupported in this phase, even if an input file claims calibration. Full
    catalogue/model provenance is retained in metadata; execution details stay out
    of protected mathematical dataclasses. Raises CatalogueError or PermissionError.
    """
    if type(research_mode) is not bool:
        raise CatalogueError('research_mode must be a boolean')
    try:
        base = Path(directory)
        knowledge = yaml.load((base / 'knowledge.yaml').read_text(), Loader=_UniqueSafeLoader)
        definitions = yaml.load((base / 'tests.yaml').read_text(), Loader=_UniqueSafeLoader)
        model = json.loads((base / 'probabilities.json').read_text(), object_pairs_hook=_unique_pairs)
        documents = {'knowledge': knowledge, 'tests': definitions, 'model': model}
        for label, doc in documents.items():
            _mapping(doc, label)
            if type(doc['schema_version']) is not int or doc['schema_version'] != 1:
                raise CatalogueError(f'Unsupported {label} schema version')
            _text(doc['version'], f'{label} version')
        _text(knowledge['catalogue_id'], 'knowledge catalogue ID')
        _text(definitions['catalogue_id'], 'test catalogue ID')
        _text(model['model_id'], 'model ID')
        _text(model['status'], 'model status')
        hs = _mapping(knowledge['hypotheses'], 'hypotheses')
        ts = _mapping(definitions['tests'], 'tests')
        prior = _mapping(model['priors'], 'priors')
        tables = _mapping(model['likelihoods'], 'likelihoods')
        for hid, entry in hs.items():
            _identifier(hid)
            _mapping(entry, hid)
            _text(entry['description'], f'{hid} description')
        if set(prior) != set(hs):
            raise CatalogueError('Prior hypothesis IDs differ from knowledge')
        for hid, value in prior.items():
            _number(value, f'prior {hid}')
        validate_distribution(prior, 'prior')
        if set(tables) != set(ts):
            raise CatalogueError('Likelihood test IDs differ from test catalogue')
        tests = {}
        for tid, entry in ts.items():
            _identifier(tid)
            _mapping(entry, tid)
            _text(entry['description'], f'{tid} description')
            outcomes = entry['outcomes']
            if not isinstance(outcomes, list):
                raise CatalogueError(f'{tid} outcomes must be a list')
            for outcome in outcomes:
                _identifier(outcome)
            if entry['normal_outcome'] not in outcomes:
                raise CatalogueError(f'{tid}: invalid normal outcome')
            rows = _mapping(tables[tid], f'{tid} likelihoods')
            if set(rows) != set(hs):
                raise CatalogueError(f'{tid}: incomplete or unknown likelihood hypotheses')
            for hid, row in rows.items():
                _mapping(row, f'{tid}/{hid}')
                for value in row.values():
                    _number(value, f'{tid}/{hid} probability')
            costs = _mapping(entry['synthetic_cost_for_planner_simulation'], f'{tid} costs')
            _text(costs['status'], f'{tid} cost status')
            energy = _number(costs['energy_j'], f'{tid} energy')
            latency = _number(costs['latency_ms'], f'{tid} latency') / 1000
            availability = _mapping(entry['availability'], f'{tid} availability')
            if any(type(availability[k]) is not bool for k in ('simulation', 'live')):
                raise CatalogueError(f'{tid} availability flags must be boolean')
            execution = _mapping(entry['execution'], f'{tid} execution')
            _text(entry['provider'], f'{tid} provider')
            _mapping(entry['target_selector'], f'{tid} target selector')
            _text(entry['evidence_group'], f'{tid} evidence group')
            if not isinstance(entry['source_basis'], list) or not entry['source_basis']:
                raise CatalogueError(f'{tid} requires source provenance')
            _text(execution['mode'], f'{tid} execution mode')
            if type(execution['requires_configuration']) is not bool:
                raise CatalogueError(f'{tid} requires_configuration must be boolean')
            _text(execution['logical_operation'], f'{tid} logical operation')
            _text(execution['binding_status'], f'{tid} binding status')
            binding = execution['live_binding']  # Required even when explicitly null.
            if availability['live'] and (execution['binding_status'] != 'verified' or not binding):
                raise CatalogueError(f'{tid}: live availability requires a verified binding')
            if entry['freshness_max_seconds'] is not None:
                _number(entry['freshness_max_seconds'], f'{tid} freshness')
            tests[tid] = DiagnosticTest(tid, tuple(outcomes), rows, energy, latency, entry['description'])
        relationships = knowledge['relationships']
        if not isinstance(relationships, list):
            raise CatalogueError('relationships must be a list')
        for relationship in relationships:
            tid = relationship['test_id']
            if tid not in tests:
                raise CatalogueError(f'Unknown relationship test: {tid}')
            for hid, labels in _mapping(relationship['hypothesis_outcomes'], 'relationship hypotheses').items():
                if hid not in hs:
                    raise CatalogueError(f'Unknown relationship hypothesis: {hid}')
                if set(labels) != {'supports', 'contradicts'} or not set(labels.values()) <= set(tests[tid].outcomes):
                    raise CatalogueError(f'{tid}/{hid}: invalid relationship outcomes')
                if labels['supports'] == labels['contradicts']:
                    raise CatalogueError(f'{tid}/{hid}: contradictory relationship')
        policy = _mapping(model['usage_policy'], 'model usage policy')
        if any(type(policy[k]) is not bool for k in ('simulation', 'live_diagnosis')):
            raise CatalogueError('Model usage flags must be boolean')
        if not research_mode:
            raise PermissionError('Phase 1 requires explicit research_mode=True; production diagnosis is disabled')
        if not policy['simulation']:
            raise PermissionError('Model usage policy forbids research/simulation inference')
        return LoadedCatalogues(
            tuple(Hypothesis(hid, e['description']) for hid, e in hs.items()),
            dict(prior), tests, ts, documents, True)
    except PermissionError:
        raise
    except (OSError, yaml.YAMLError, ValueError, TypeError, KeyError, OverflowError) as error:
        if isinstance(error, CatalogueError):
            raise
        raise CatalogueError(f'Invalid catalogue: {error}') from error
