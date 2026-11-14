
import pytest

from backend.core.models import ExecutionStatus, DiagnosticTest, Observation
from backend.diagnosis.bayesian import (
    apply_observation,
    bayesian_update,
    entropy,
    expected_information_gain,
)


@pytest.fixture
def diagnostic_test():
    return DiagnosticTest(
        identifier="check_worker",
        outcomes=("running", "stopped"),
        likelihoods={
            "worker_ok": {"running": 0.9, "stopped": 0.1},
            "worker_failed": {"running": 0.2, "stopped": 0.8},
        },
        expected_energy_joules=1.0,
        expected_latency_seconds=0.5,
    )


@pytest.fixture
def prior():
    return {"worker_ok": 0.5, "worker_failed": 0.5}


def test_bayesian_update_returns_normalized_distribution(prior, diagnostic_test):
    posterior = bayesian_update(prior, diagnostic_test, "stopped")

    assert set(posterior) == set(prior)
    assert sum(posterior.values()) == pytest.approx(1.0)


def test_bayesian_update_increases_failed_probability(prior, diagnostic_test):
    posterior = bayesian_update(prior, diagnostic_test, "stopped")

    assert posterior["worker_failed"] > prior["worker_failed"]


def test_bayesian_update_increases_ok_probability_for_running(prior, diagnostic_test):
    posterior = bayesian_update(prior, diagnostic_test, "running")

    assert posterior["worker_ok"] > prior["worker_ok"]


def test_bayesian_update_rejects_unknown_outcome(prior, diagnostic_test):
    with pytest.raises(ValueError):
        bayesian_update(prior, diagnostic_test, "unknown")


def test_bayesian_update_rejects_mismatched_hypotheses(diagnostic_test):
    with pytest.raises(ValueError):
        bayesian_update(
            {"other": 1.0},
            diagnostic_test,
            "running",
        )


def test_bayesian_update_rejects_zero_predictive_probability():
    test = DiagnosticTest(
        identifier="impossible",
        outcomes=("yes", "no"),
        likelihoods={
            "a": {"yes": 1.0, "no": 0.0},
            "b": {"yes": 1.0, "no": 0.0},
        },
        expected_energy_joules=0,
        expected_latency_seconds=0,
    )

    with pytest.raises(ValueError):
        bayesian_update({"a": 0.5, "b": 0.5}, test, "no")


def test_apply_observation_updates_completed_observation(prior, diagnostic_test):
    observation = Observation(
        test_identifier="check_worker",
        target_identifier="worker",
        execution_status=ExecutionStatus.COMPLETED,
        observed_outcome="stopped",
    )

    posterior = apply_observation(prior, diagnostic_test, observation)

    assert posterior["worker_failed"] > prior["worker_failed"]


@pytest.mark.parametrize(
    "status",
    [ExecutionStatus.FAILED, ExecutionStatus.UNAVAILABLE],
)
def test_apply_observation_does_not_update_on_failure_or_unavailability(
    prior, diagnostic_test, status
):
    observation = Observation(
        test_identifier="check_worker",
        target_identifier="worker",
        execution_status=status,
    )

    posterior = apply_observation(prior, diagnostic_test, observation)

    assert posterior == prior
    assert posterior is not prior


def test_apply_observation_rejects_test_identifier_mismatch(prior, diagnostic_test):
    observation = Observation(
        test_identifier="another_test",
        target_identifier="worker",
        execution_status=ExecutionStatus.COMPLETED,
        observed_outcome="stopped",
    )

    with pytest.raises(ValueError):
        apply_observation(prior, diagnostic_test, observation)


def test_entropy_of_uniform_distribution():
    assert entropy({"a": 0.5, "b": 0.5}) == pytest.approx(1.0)


def test_entropy_of_certain_distribution_is_zero():
    assert entropy({"a": 1.0, "b": 0.0}) == pytest.approx(0.0)


def test_entropy_rejects_invalid_distribution():
    with pytest.raises(ValueError):
        entropy({"a": 0.2, "b": 0.2})


def test_expected_information_gain_is_nonnegative(prior, diagnostic_test):
    gain = expected_information_gain(prior, diagnostic_test)

    assert gain >= 0


def test_expected_information_gain_is_positive_for_informative_test(
    prior, diagnostic_test
):
    assert expected_information_gain(prior, diagnostic_test) > 0


def test_expected_information_gain_is_zero_for_uninformative_test():
    test = DiagnosticTest(
        identifier="uninformative",
        outcomes=("yes", "no"),
        likelihoods={
            "a": {"yes": 0.5, "no": 0.5},
            "b": {"yes": 0.5, "no": 0.5},
        },
        expected_energy_joules=1,
        expected_latency_seconds=1,
    )

    assert expected_information_gain(
        {"a": 0.5, "b": 0.5}, test
    ) == pytest.approx(0.0)


def test_expected_information_gain_rejects_mismatched_hypotheses(
    diagnostic_test,
):
    with pytest.raises(ValueError):
        expected_information_gain({"other": 1.0}, diagnostic_test)
