"""Independent references: exact fractions for Bayes; direct mutual information for IG."""
from copy import deepcopy
from dataclasses import asdict, replace
from fractions import Fraction as F
import json
import math
import random

import pytest

from backend.core.models import DiagnosticTest, Decision, ExecutionStatus, Observation
from backend.core.orchestrator import StoppingConfig, run_investigation, stopping_decision
from backend.diagnosis.bayesian import bayesian_update, entropy, expected_information_gain
from backend.optimization.planner import DiagnosticPolicy
from experiments.compare_policies import (SCENARIOS, Incident, brier_score, evaluate,
    experiment_model, generate_incidents, reliability_bins, summarize)


def reference_posterior(prior, rows, outcome):
    products = {h: prior[h] * rows[h][outcome] for h in prior}
    total = sum(products.values(), F(0))
    return {h: float(p/total) for h, p in products.items()}


def direct_mutual_information(prior, rows):
    # Independent identity: sum_h,o p(h)L(o|h) log2[L(o|h)/p(o)].
    total = 0.0
    for outcome in next(iter(rows.values())):
        predictive = sum(prior[h] * rows[h][outcome] for h in prior)
        for h, p in prior.items():
            joint = p * rows[h][outcome]
            if joint:
                total += float(joint) * math.log2(float(rows[h][outcome]/predictive))
    return total


def test_fraction_reference_three_hypotheses_and_sequential_product():
    prior = {'a': F(1, 2), 'b': F(1, 3), 'c': F(1, 6)}
    rows = {'a': {'x': F(1, 2), 'y': F(1, 3), 'z': F(1, 6)},
            'b': {'x': F(1, 4), 'y': F(1, 2), 'z': F(1, 4)},
            'c': {'x': F(0), 'y': F(1, 4), 'z': F(3, 4)}}
    test = DiagnosticTest('first', ('x', 'y', 'z'),
        {h: {o: float(p) for o, p in row.items()} for h, row in rows.items()}, 1, 1)
    p = {h: float(v) for h, v in prior.items()}
    original = deepcopy(p)
    # Evidence x has probability 1/3: posterior = (3/4,1/4,0).
    assert bayesian_update(p, test, 'x') == pytest.approx({'a': .75, 'b': .25, 'c': 0})
    for outcome in test.outcomes:
        actual = bayesian_update(p, test, outcome)
        assert actual == pytest.approx(reference_posterior(prior, rows, outcome), abs=1e-14)
        assert math.fsum(actual.values()) == pytest.approx(1, abs=1e-14)
    # Product likelihood reference for two distinct conditionally independent tests.
    products = {h: prior[h] * rows[h]['y'] * rows[h]['z'] for h in prior}
    expected = {h: float(v / sum(products.values())) for h, v in products.items()}
    sequential = bayesian_update(bayesian_update(p, test, 'y'), replace(test, identifier='second'), 'z')
    reverse = bayesian_update(bayesian_update(p, replace(test, identifier='second'), 'z'), test, 'y')
    assert sequential == pytest.approx(expected, abs=1e-14)
    assert reverse == pytest.approx(expected, abs=1e-14)
    assert expected_information_gain(p, test) == pytest.approx(direct_mutual_information(prior, rows), abs=1e-14)
    assert p == original


def test_random_rational_models_against_independent_mutual_information():
    rng = random.Random(2026)
    for _ in range(60):
        weights = [rng.randrange(1, 20) for _ in range(4)]
        prior = {str(i): F(w, sum(weights)) for i, w in enumerate(weights)}
        rows = {}
        for h in prior:
            counts = [rng.randrange(0, 20) for _ in range(3)]
            if sum(counts) == 0:
                counts[0] = 1
            rows[h] = {str(o): F(w, sum(counts)) for o, w in enumerate(counts)}
        test = DiagnosticTest('t', ('0', '1', '2'),
            {h: {o: float(p) for o, p in row.items()} for h, row in rows.items()}, 1, 1)
        p = {h: float(v) for h, v in prior.items()}
        assert expected_information_gain(p, test) == pytest.approx(direct_mutual_information(prior, rows), abs=2e-14)
        for outcome in test.outcomes:
            if sum(prior[h]*rows[h][outcome] for h in prior):
                assert bayesian_update(p, test, outcome) == pytest.approx(reference_posterior(prior, rows, outcome), abs=2e-14)


def test_entropy_independent_constant_and_information_can_increase_on_one_outcome():
    assert entropy({'a': .5, 'b': .25, 'c': .25}) == pytest.approx(1.5)
    test = DiagnosticTest('t', ('surprise', 'ordinary'),
        {'a': {'surprise': .01, 'ordinary': .99}, 'b': {'surprise': .99, 'ordinary': .01}}, 1, 1)
    posterior = bayesian_update({'a': .99, 'b': .01}, test, 'surprise')
    assert posterior == pytest.approx({'a': .5, 'b': .5})
    assert entropy(posterior) == pytest.approx(1)
    assert expected_information_gain({'a': .99, 'b': .01}, test) >= 0


def test_impossible_and_zero_prior_cannot_recover():
    test = DiagnosticTest('t', ('yes', 'no'),
        {'a': {'yes': .9, 'no': .1}, 'b': {'yes': .1, 'no': .9}}, 1, 1)
    assert bayesian_update({'a': 0, 'b': 1}, test, 'yes') == {'a': 0, 'b': 1}
    with pytest.raises(ValueError, match='zero predictive'):
        bayesian_update({'a': 0, 'b': 1}, replace(test,
            likelihoods={'a': {'yes': 1, 'no': 0}, 'b': {'yes': 0, 'no': 1}}), 'yes')


@pytest.mark.parametrize('confidence,expected', [(.949999, Decision.ABSTAIN), (.95, Decision.DIAGNOSED), (.950001, Decision.DIAGNOSED)])
def test_threshold_boundary(confidence, expected):
    decision, _ = stopping_decision({'a': confidence, 'b': 1-confidence}, [], 1, 1, 1, StoppingConfig())
    assert decision == expected


def test_no_evidence_overrides_confident_prior_by_default():
    assert stopping_decision({'a': .99, 'b': .01}, [], 0, 0, 0, StoppingConfig())[0] == Decision.ABSTAIN


def test_tie_decision_and_result_use_same_leader():
    p = {'other_unknown': .5, 'known': .5}  # Deliberately opposite insertion order.
    config = StoppingConfig(confidence_threshold=.5, minimum_successful_observations=0)
    result = run_investigation(p, [], DiagnosticPolicy(), 'known', stopping=config)
    assert result.decision == Decision.DIAGNOSED
    assert result.diagnosed_hypothesis == 'known'
    p = {'other_unknown': .5, 'z_known': .5}
    result = run_investigation(p, [], DiagnosticPolicy(), 'z_known', stopping=config)
    assert result.decision == Decision.ABSTAIN
    assert result.diagnosed_hypothesis is None


def test_free_test_selection_strict_json():
    test = DiagnosticTest('free', ('yes', 'no'),
        {'a': {'yes': 1, 'no': 0}, 'b': {'yes': 0, 'no': 1}}, 0, 0)
    selection = DiagnosticPolicy('ratio').select({'a': .5, 'b': .5}, [test])
    assert selection.zero_cost_priority and selection.score is None
    assert json.loads(json.dumps(asdict(selection), allow_nan=False))['score'] is None


@pytest.mark.parametrize('kwargs', [dict(confidence_threshold=float('nan')), dict(confidence_threshold=0),
    dict(confidence_threshold=1.01), dict(max_tests=-1), dict(max_tests=1.5),
    dict(max_energy_joules=float('inf')), dict(max_energy_joules=-1),
    dict(minimum_information_gain_bits=float('nan')), dict(minimum_successful_observations=-1)])
def test_invalid_stopping_config(kwargs):
    with pytest.raises(ValueError):
        StoppingConfig(**kwargs)


def test_correlated_duplicate_evidence_overconfidence_reference():
    # True O1=O2 is one shared Bernoulli: P(yes,yes|a)=.8, not .64.
    # Under a uniform prior the correct joint posterior is .8; independence gives 16/17.
    test = DiagnosticTest('t1', ('yes', 'no'),
        {'a': {'yes': .8, 'no': .2}, 'b': {'yes': .2, 'no': .8}}, 1, 1)
    assumed = bayesian_update(bayesian_update({'a': .5, 'b': .5}, test, 'yes'),
                              replace(test, identifier='t2'), 'yes')
    true_posterior = F(4, 5) / (F(4, 5) + F(1, 5))
    assert assumed['a'] == pytest.approx(float(F(16, 17)))
    assert assumed['a'] > float(true_posterior)


def test_brier_and_bins_hand_worked():
    # Scores: (.8-1)^2+.2^2=.08; .6^2+(.4-1)^2=.72.
    assert brier_score({'a': .8, 'b': .2}, 'a') == pytest.approx(.08)
    assert brier_score({'a': .6, 'b': .4}, 'b') == pytest.approx(.72)
    bins = reliability_bins([{'a': .8, 'b': .2}, {'a': .6, 'b': .4}, {'a': 1, 'b': 0}], ['a', 'b', 'a'])
    assert bins[8] == dict(lower=.8, upper=.9, count=1, mean_confidence=.8, observed_accuracy=1)
    assert bins[6]['count'] == 1 and bins[6]['observed_accuracy'] == 0
    assert bins[9]['count'] == 1 and bins[9]['mean_confidence'] == 1
    assert bins[0]['mean_confidence'] is None
    with pytest.raises(ValueError):
        brier_score({'a': 1}, 'external')
    with pytest.raises(ValueError):
        reliability_bins([{'a': 1}], [])


def test_metrics_denominators_and_ood_mapping():
    test = DiagnosticTest('t', ('a', 'b'),
        {'a': {'a': 1, 'b': 0}, 'b': {'a': 0, 'b': 1},
         'other_unknown': {'a': .5, 'b': .5}}, 1, 2)
    def result(p, outcome):
        obs = Observation('t', 'synthetic-voting-app', ExecutionStatus.COMPLETED, outcome)
        return run_investigation(p, [test], DiagnosticPolicy(), predefined_observations={'t': obs})
    correct = result({'a': .5, 'b': .5, 'other_unknown': 0}, 'a')
    wrong = result({'a': .5, 'b': .5, 'other_unknown': 0}, 'a')
    abstain = replace(correct, decision=Decision.ABSTAIN, diagnosed_hypothesis=None,
                      final_beliefs={'a': 0, 'b': 0, 'other_unknown': 1})
    summary = summarize([correct, wrong, abstain], [Incident('a', {}), Incident('b', {}), Incident('external', {})])
    assert summary['overall_accuracy'] == pytest.approx(1/3)
    assert summary['diagnosed_accuracy'] == .5
    assert summary['abstention_rate'] == pytest.approx(1/3)
    assert summary['total_estimated_energy_joules'] == 3
    assert summary['energy_per_correct'] == 3
    assert summary['total_estimated_latency_seconds'] == 6
    assert summary['brier_score'] == pytest.approx(2/3)
    assert summary['outside_catalogue_false_diagnosis_rate'] == 0
    assert summary['top_label_accuracy'] == pytest.approx(2/3)
    assert summarize([abstain], [Incident('external', {})])['energy_per_correct'] is None
    assert summarize([abstain], [Incident('external', {})])['diagnosed_accuracy'] is None


def test_true_model_is_separate_and_correlation_preserves_rows():
    matched = experiment_model('matched')
    correlated = experiment_model('correlated')
    assert matched.true_likelihoods == correlated.true_likelihoods
    assert matched.shared_quantile_probability == 0
    assert correlated.shared_quantile_probability == 1
    matched.true_likelihoods['check_worker_state']['worker_stopped']['stopped'] = .5
    assert matched.assumed_tests[0].likelihoods['worker_stopped']['stopped'] == .98
    ood = experiment_model('out_of_catalogue')
    assert 'novel_network_partition' in ood.true_prior
    assert 'novel_network_partition' not in ood.assumed_prior


@pytest.mark.parametrize('scenario', SCENARIOS)
def test_paired_reproducible_robustness_and_termination(scenario):
    model = experiment_model(scenario)
    records = generate_incidents(model, 12, 1234)
    assert records == generate_incidents(model, 12, 1234)
    snapshot = deepcopy(records)
    rows = evaluate(model, records, StoppingConfig(max_energy_joules=6))
    assert len({r['incident_fingerprint'] for r in rows}) == 1
    assert records == snapshot
    for row in rows:
        assert row['mean_energy_joules'] <= 6
        assert row['mean_tests'] <= 4
        assert sum(row['termination_reasons'].values()) == 12
        assert row['correct'] <= row['diagnosed'] <= 12
        assert 0 <= row['brier_score'] <= 2
        assert sum(b['count'] for b in row['reliability_bins']) == 12
    # Zero budget makes all policies abstain; no conditioning on diagnostic success.
    zero = evaluate(model, records, StoppingConfig(max_energy_joules=0))
    assert all(r['abstention_rate'] == 1 and r['mean_tests'] == 0 for r in zero)


@pytest.mark.parametrize('budget', [100, 6])
def test_exhaustive_matched_joint_model_terminal_posteriors_and_calibration(budget):
    """Enumerate every potential outcome vector; no Monte Carlo or engine reference.

    Compute exact joint masses with Fraction and verify the posterior given ONLY
    selected observations. Then integrate confidence/correctness over ALL worlds.
    Under a correctly specified model, adaptive stopping preserves calibration.
    """
    from itertools import product
    model = experiment_model('matched')
    prior = {h: F(str(p)).limit_denominator(10**6) for h, p in model.true_prior.items()}
    likelihoods = {t.identifier: {h: {o: F(str(p)).limit_denominator(10**6)
        for o, p in row.items()} for h, row in t.likelihoods.items()} for t in model.assumed_tests}
    for method in ('fixed', 'information_gain', 'ratio', 'weighted'):
        groups = [[0.0, 0.0, 0.0] for _ in range(10)]
        total_probability = F(0)
        for outcomes in product(*(t.outcomes for t in model.assumed_tests)):
            observations = {t.identifier: Observation(t.identifier, 'synthetic-voting-app',
                ExecutionStatus.COMPLETED, o) for t, o in zip(model.assumed_tests, outcomes)}
            full_mass = dict(prior)
            for t, o in zip(model.assumed_tests, outcomes):
                full_mass = {h: p * likelihoods[t.identifier][h][o] for h, p in full_mass.items()}
            world_mass = sum(full_mass.values(), F(0))
            total_probability += world_mass
            result = run_investigation(model.assumed_prior, model.assumed_tests,
                DiagnosticPolicy(method, tuple(t.identifier for t in model.assumed_tests), .02, .01),
                stopping=StoppingConfig(max_energy_joules=budget), predefined_observations=observations)
            prefix_mass = dict(prior)
            for observation in result.observations:
                prefix_mass = {h: p * likelihoods[observation.test_identifier][h][observation.observed_outcome]
                               for h, p in prefix_mass.items()}
            expected = {h: float(p / sum(prefix_mass.values())) for h, p in prefix_mass.items()}
            assert result.final_beliefs == pytest.approx(expected, abs=2e-14)
            leader = sorted(expected, key=lambda h: (-expected[h], h))[0]
            confidence = expected[leader]
            group = groups[min(int(confidence * 10), 9)]
            group[0] += float(world_mass)
            group[1] += confidence * float(world_mass)
            group[2] += float(full_mass[leader])
        assert total_probability == 1
        for mass, confidence_mass, correct_mass in groups:
            if mass:
                assert confidence_mass / mass == pytest.approx(correct_mass / mass, abs=2e-14)
