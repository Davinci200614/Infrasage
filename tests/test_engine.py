import math
from dataclasses import replace
import pytest
from backend.core.models import (DiagnosticTest, Observation, ExecutionStatus, Decision,
                                 validate_distribution)
from backend.core.orchestrator import run_investigation, stopping_decision, StoppingConfig
from backend.diagnosis.bayesian import (bayesian_update, entropy, expected_information_gain,
                                        apply_observation)
from backend.diagnosis.example import synthetic_model
from backend.optimization.planner import DiagnosticPolicy
from experiments.compare_policies import compare


def binary(identifier='test', a=0.8, b=0.2, energy=1, latency=1):
    return DiagnosticTest(identifier, ('yes', 'no'),
                          {'a': {'yes': a, 'no': 1-a}, 'b': {'yes': b, 'no': 1-b}}, energy, latency)


@pytest.mark.parametrize('prior', [{}, {'a': -0.1, 'b': 1.1}, {'a': float('nan')},
                                  {'a': float('inf')}, {'a': 0.9}, {'a': 0, 'b': 0}])
def test_invalid_priors(prior):
    with pytest.raises(ValueError):
        validate_distribution(prior)


@pytest.mark.parametrize('rows', [ {}, {'a': {'yes': 1}},
    {'a': {'yes': 0.8, 'no': 0.8}}, {'a': {'yes': -0.1, 'no': 1.1}},
    {'a': {'yes': float('nan'), 'no': 0}}])
def test_invalid_likelihoods(rows):
    with pytest.raises(ValueError):
        DiagnosticTest('t', ('yes', 'no'), rows, 1, 1)


@pytest.mark.parametrize('value', [-1, float('nan'), float('inf')])
def test_invalid_costs(value):
    with pytest.raises(ValueError):
        binary(energy=value)
    with pytest.raises(ValueError):
        binary(latency=value)


def test_hand_worked_bayes_and_sequence():
    prior = {'a': 0.6, 'b': 0.4}
    test = binary()
    # P(yes)=.8*.6+.2*.4=.56; posterior(a)=.48/.56=6/7.
    posterior = bayesian_update(prior, test, 'yes')
    assert posterior == pytest.approx({'a': 6/7, 'b': 1/7})
    assert prior == {'a': 0.6, 'b': 0.4}
    assert sum(posterior.values()) == pytest.approx(1)
    # Distinct independent test with same likelihoods: .384/(.384+.016)=.96.
    assert bayesian_update(posterior, binary('second'), 'yes')['a'] == pytest.approx(0.96)


def test_zero_evidence_and_unsupported():
    with pytest.raises(ValueError, match='zero predictive'):
        bayesian_update({'a': 0.5, 'b': 0.5}, binary(a=0, b=0), 'yes')
    with pytest.raises(ValueError, match='Unsupported'):
        bayesian_update({'a': 0.5, 'b': 0.5}, binary(), 'maybe')
    with pytest.raises(ValueError, match='same hypotheses'):
        bayesian_update({'x': 1}, binary(), 'yes')


def test_log_normalization():
    test = binary(a=1e-300, b=2e-300)
    result = bayesian_update({'a': 1e-100, 'b': 1.0}, test, 'yes')
    assert result['a'] > 0
    assert sum(result.values()) == pytest.approx(1)


def test_entropy():
    assert entropy({'a': 1, 'b': 0}) == 0
    for n in (2, 3, 8):
        assert entropy({str(i): 1/n for i in range(n)}) == pytest.approx(math.log2(n))


def test_information_gain():
    prior = {'a': 0.6, 'b': 0.4}
    assert expected_information_gain(prior, binary(a=0.5, b=0.5)) == pytest.approx(0, abs=1e-12)
    assert expected_information_gain(prior, binary(a=1, b=0)) == pytest.approx(entropy(prior))
    assert expected_information_gain(prior, binary(a=0, b=0)) == pytest.approx(0, abs=1e-12)
    # Uniform prior, binary symmetric channel: IG=1-H(.8,.2).
    assert expected_information_gain({'a': 0.5, 'b': 0.5}, binary()) == pytest.approx(1-entropy({'a': .8, 'b': .2}))
    for a in (0, .1, .5, 1):
        for b in (0, .3, 1):
            assert expected_information_gain(prior, binary(a=a, b=b)) >= 0


def test_three_outcomes():
    test = DiagnosticTest('multi', ('a', 'b', 'unused'),
        {'a': {'a': 1, 'b': 0, 'unused': 0}, 'b': {'a': 0, 'b': 1, 'unused': 0}}, 1, 1)
    assert expected_information_gain({'a': .5, 'b': .5}, test) == pytest.approx(1)


def test_policies_and_ties():
    prior = {'a': .5, 'b': .5}
    cheap, expensive = binary('cheap'), binary('expensive', a=1, b=0, energy=10)
    assert DiagnosticPolicy('information_gain').select(prior, [cheap, expensive]).test == expensive
    assert DiagnosticPolicy('ratio').select(prior, [cheap, expensive]).test == cheap
    assert DiagnosticPolicy('weighted', lambda_energy=.1).select(prior, [cheap, expensive]).test == cheap
    assert DiagnosticPolicy('fixed', ('expensive', 'cheap')).select(prior, [cheap, expensive]).test == expensive
    assert DiagnosticPolicy('weighted', lambda_latency=1).select(prior, [cheap, replace(expensive, expected_latency_seconds=10)]).test == cheap
    for method in ('information_gain', 'ratio', 'weighted'):
        for tests in ([binary('z'), binary('a')], [binary('a'), binary('z')]):
            assert DiagnosticPolicy(method).select(prior, tests).test.identifier == 'a'
    free = binary('free', energy=0)
    assert DiagnosticPolicy('ratio').select(prior, [cheap, free]).test == free
    assert DiagnosticPolicy('ratio').select(prior, [binary('zero', a=.5, b=.5, energy=0), cheap]).test == cheap


def test_stopping_and_budgets():
    prior = {'a': .5, 'b': .5}
    policy = DiagnosticPolicy()
    result = run_investigation(prior, [binary()], policy, 'a', stopping=StoppingConfig(max_energy_joules=.5))
    assert result.decision == Decision.ABSTAIN and not result.observations
    assert 'budget' in result.reason
    result = run_investigation(prior, [binary(a=1, b=0)], policy, 'a')
    assert result.decision == Decision.DIAGNOSED
    assert len(result.observations) == 1
    assert result.total_estimated_energy_joules == 1
    assert len(result.belief_history) == 2
    result = run_investigation(prior, [binary()], policy, 'a', stopping=StoppingConfig(max_tests=0))
    assert result.decision == Decision.ABSTAIN
    result = run_investigation(prior, [binary(a=.5, b=.5)], policy, 'a')
    assert 'insufficient evidence' in result.reason
    decision, _ = stopping_decision({'a': .01, 'other_unknown': .99}, [], 1, 1, 1, StoppingConfig())
    assert decision == Decision.ABSTAIN
    result = run_investigation(prior, [], policy, 'a')
    assert result.decision == Decision.ABSTAIN


@pytest.mark.parametrize('status', [ExecutionStatus.FAILED, ExecutionStatus.UNAVAILABLE])
def test_collector_errors(status):
    prior = {'a': .5, 'b': .5}
    obs = Observation('test', 'target', status)
    assert apply_observation(prior, binary(), obs) == prior
    result = run_investigation(prior, [binary()], DiagnosticPolicy(),
        predefined_observations={'test': obs}, target_identifier='target')
    assert result.final_beliefs == prior
    assert result.decision == Decision.ABSTAIN
    assert result.total_estimated_energy_joules == 1
    assert len(result.observations) == 1


def test_completed_unhealthy_is_evidence():
    obs = Observation('test', 'target', ExecutionStatus.COMPLETED, 'no')
    assert apply_observation({'a': .5, 'b': .5}, binary(), obs)['b'] == pytest.approx(.8)
    with pytest.raises(ValueError):
        Observation('test', 'target', ExecutionStatus.FAILED, 'no')


def test_reproducibility_and_unique_tests():
    prior, tests = synthetic_model()
    args = (prior, tests, DiagnosticPolicy(), 'redis_unavailable')
    a, b = run_investigation(*args, seed=7), run_investigation(*args, seed=7)
    assert a == b
    assert len({o.test_identifier for o in a.observations}) == len(a.observations)
    assert compare(20, 42) == compare(20, 42)
    with pytest.raises(ValueError, match='Duplicate'):
        run_investigation(prior, tests + tests, DiagnosticPolicy(), 'redis_unavailable')


def test_predefined_validation():
    with pytest.raises(ValueError, match='Missing predefined'):
        run_investigation({'a': .5, 'b': .5}, [binary()], DiagnosticPolicy(), predefined_observations={})


def test_budget_filters_unaffordable_tests_before_scoring():
    result = run_investigation({'a': .5, 'b': .5},
        [binary('expensive', a=1, b=0, energy=10), binary('cheap', a=1, b=0)],
        DiagnosticPolicy(), 'a', stopping=StoppingConfig(max_energy_joules=1))
    assert result.selections[0].test.identifier == 'cheap'
    assert result.total_estimated_energy_joules == 1
    assert result.decision == Decision.DIAGNOSED


def test_unknown_investigation_abstains():
    test = DiagnosticTest('unknown_check', ('unknown', 'known'),
        {'other_unknown': {'unknown': 1, 'known': 0}, 'known_cause': {'unknown': 0, 'known': 1}}, 1, 1)
    result = run_investigation({'other_unknown': .5, 'known_cause': .5}, [test],
                               DiagnosticPolicy(), 'other_unknown')
    assert result.decision == Decision.ABSTAIN
    assert result.diagnosed_hypothesis is None
    assert result.final_beliefs['other_unknown'] == 1


@pytest.mark.parametrize('method', ['fixed', 'information_gain', 'ratio', 'weighted'])
def test_all_policies_end_to_end(method):
    prior, tests = synthetic_model()
    policy = DiagnosticPolicy(method, tuple(t.identifier for t in tests))
    for truth in prior:
        result = run_investigation(prior, tests, policy, truth, seed=42)
        assert result.decision in (Decision.DIAGNOSED, Decision.ABSTAIN)
        assert len(result.observations) <= 4
        assert sum(result.final_beliefs.values()) == pytest.approx(1)
        assert result.total_estimated_energy_joules <= 100
