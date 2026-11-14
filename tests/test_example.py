
import pytest

from backend.diagnosis.example import synthetic_model
from backend.core.models import DiagnosticTest, validate_distribution


def test_synthetic_model_returns_prior_and_tests():
    prior, tests = synthetic_model()

    assert isinstance(prior, dict)
    assert isinstance(tests, list)
    assert tests


def test_synthetic_model_prior_is_valid():
    prior, _ = synthetic_model()

    validate_distribution(prior)


def test_synthetic_model_contains_four_hypotheses():
    prior, _ = synthetic_model()

    assert set(prior) == {
        "worker_stopped",
        "redis_unavailable",
        "database_unavailable",
        "other_unknown",
    }


def test_synthetic_model_contains_expected_tests():
    _, tests = synthetic_model()

    identifiers = {test.identifier for test in tests}

    assert identifiers == {
        "check_worker_state",
        "check_redis_connectivity",
        "check_database_connectivity",
        "check_application_workflow",
    }


def test_synthetic_model_tests_are_valid():
    _, tests = synthetic_model()

    for test in tests:
        assert isinstance(test, DiagnosticTest)
        test.__post_init__()


def test_synthetic_model_likelihoods_cover_all_hypotheses():
    prior, tests = synthetic_model()

    for test in tests:
        assert set(test.likelihoods) == set(prior)


def test_synthetic_model_likelihoods_are_valid():
    _, tests = synthetic_model()

    for test in tests:
        for row in test.likelihoods.values():
            validate_distribution(row)


def test_synthetic_model_uses_illustrative_costs():
    _, tests = synthetic_model()

    assert all(test.expected_energy_joules >= 0 for test in tests)
    assert all(test.expected_latency_seconds >= 0 for test in tests)


def test_synthetic_model_returns_independent_instances():
    prior_a, tests_a = synthetic_model()
    prior_b, tests_b = synthetic_model()

    assert prior_a == prior_b
    assert tests_a == tests_b
    assert prior_a is not prior_b
    assert tests_a is not tests_b
