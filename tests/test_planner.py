
import pytest

from backend.core.models import DiagnosticTest
from backend.optimization.planner import DiagnosticPolicy


@pytest.fixture
def prior():
    return {
        "worker_ok": 0.5,
        "worker_failed": 0.5,
    }


@pytest.fixture
def informative_test():
    return DiagnosticTest(
        identifier="informative",
        outcomes=("running", "stopped"),
        likelihoods={
            "worker_ok": {"running": 0.9, "stopped": 0.1},
            "worker_failed": {"running": 0.2, "stopped": 0.8},
        },
        expected_energy_joules=2.0,
        expected_latency_seconds=1.0,
    )


@pytest.fixture
def weak_test():
    return DiagnosticTest(
        identifier="weak",
        outcomes=("running", "stopped"),
        likelihoods={
            "worker_ok": {"running": 0.6, "stopped": 0.4},
            "worker_failed": {"running": 0.5, "stopped": 0.5},
        },
        expected_energy_joules=1.0,
        expected_latency_seconds=0.5,
    )


def test_default_policy_is_information_gain():
    policy = DiagnosticPolicy()

    assert policy.method == "information_gain"


@pytest.mark.parametrize(
    "method",
    ["fixed", "information_gain", "ratio", "weighted"],
)
def test_supported_policy_methods(method):
    policy = DiagnosticPolicy(method=method)

    assert policy.method == method


def test_unknown_policy_method_is_rejected():
    with pytest.raises(ValueError, match="Unknown policy"):
        DiagnosticPolicy(method="unknown")


def test_duplicate_fixed_order_is_rejected():
    with pytest.raises(ValueError, match="duplicate"):
        DiagnosticPolicy(
            method="fixed",
            fixed_order=("a", "a"),
        )


@pytest.mark.parametrize(
    "kwargs",
    [
        {"lambda_energy": -1},
        {"lambda_latency": -1},
        {"lambda_energy": float("nan")},
        {"lambda_latency": float("inf")},
    ],
)
def test_invalid_policy_cost_parameters_are_rejected(kwargs):
    with pytest.raises(ValueError):
        DiagnosticPolicy(method="weighted", **kwargs)


def test_select_rejects_empty_test_list(prior):
    with pytest.raises(ValueError, match="No available tests"):
        DiagnosticPolicy().select(prior, [])


def test_select_rejects_duplicate_test_identifiers(
    prior, informative_test
):
    with pytest.raises(ValueError, match="Duplicate test"):
        DiagnosticPolicy().select(
            prior,
            [informative_test, informative_test],
        )


def test_information_gain_policy_selects_most_informative_test(
    prior, informative_test, weak_test
):
    selection = DiagnosticPolicy(method="information_gain").select(
        prior,
        [weak_test, informative_test],
    )

    assert selection.test.identifier == "informative"
    assert selection.policy == "information_gain"


def test_fixed_policy_respects_configured_order(
    prior, informative_test, weak_test
):
    policy = DiagnosticPolicy(
        method="fixed",
        fixed_order=("weak", "informative"),
    )

    selection = policy.select(
        prior,
        [informative_test, weak_test],
    )

    assert selection.test.identifier == "weak"
    assert selection.score == -0.0


def test_fixed_policy_requires_all_tests_in_order(
    prior, informative_test, weak_test
):
    policy = DiagnosticPolicy(
        method="fixed",
        fixed_order=("informative",),
    )

    with pytest.raises(ValueError, match="include every"):
        policy.select(prior, [informative_test, weak_test])


def test_ratio_policy_selects_best_information_per_energy(
    prior, informative_test, weak_test
):
    selection = DiagnosticPolicy(method="ratio").select(
        prior,
        [informative_test, weak_test],
    )

    expected_ratio = (
        selection.information_gain_bits
        / selection.expected_energy_joules
    )

    assert selection.score == pytest.approx(expected_ratio)


def test_ratio_policy_prioritizes_free_informative_test(prior):
    free_test = DiagnosticTest(
        identifier="free_informative",
        outcomes=("yes", "no"),
        likelihoods={
            "worker_ok": {"yes": 0.9, "no": 0.1},
            "worker_failed": {"yes": 0.1, "no": 0.9},
        },
        expected_energy_joules=0,
        expected_latency_seconds=0.1,
    )

    expensive_test = DiagnosticTest(
        identifier="expensive",
        outcomes=("yes", "no"),
        likelihoods={
            "worker_ok": {"yes": 0.9, "no": 0.1},
            "worker_failed": {"yes": 0.1, "no": 0.9},
        },
        expected_energy_joules=10,
        expected_latency_seconds=1,
    )

    selection = DiagnosticPolicy(method="ratio").select(
        prior,
        [expensive_test, free_test],
    )

    assert selection.test.identifier == "free_informative"
    assert selection.zero_cost_priority is True
    assert selection.score is None


def test_ratio_policy_handles_free_uninformative_test(prior):
    free_test = DiagnosticTest(
        identifier="free_uninformative",
        outcomes=("yes", "no"),
        likelihoods={
            "worker_ok": {"yes": 0.5, "no": 0.5},
            "worker_failed": {"yes": 0.5, "no": 0.5},
        },
        expected_energy_joules=0,
        expected_latency_seconds=0,
    )

    selection = DiagnosticPolicy(method="ratio").select(
        prior,
        [free_test],
    )

    assert selection.test.identifier == "free_uninformative"
    assert selection.zero_cost_priority is False
    assert selection.score == 0.0


def test_weighted_policy_calculates_score(
    prior, informative_test
):
    policy = DiagnosticPolicy(
        method="weighted",
        lambda_energy=0.1,
        lambda_latency=0.2,
    )

    selection = policy.select(prior, [informative_test])

    expected_score = (
        selection.information_gain_bits
        - 0.1 * informative_test.expected_energy_joules
        - 0.2 * informative_test.expected_latency_seconds
    )

    assert selection.score == pytest.approx(expected_score)


def test_selection_contains_cost_and_information_metrics(
    prior, informative_test
):
    selection = DiagnosticPolicy().select(prior, [informative_test])

    assert selection.information_gain_bits >= 0
    assert selection.expected_energy_joules == 2.0
    assert selection.expected_latency_seconds == 1.0


def test_selection_tie_is_resolved_deterministically(prior):
    test_b = DiagnosticTest(
        identifier="test_b",
        outcomes=("yes", "no"),
        likelihoods={
            "worker_ok": {"yes": 0.9, "no": 0.1},
            "worker_failed": {"yes": 0.1, "no": 0.9},
        },
        expected_energy_joules=1,
        expected_latency_seconds=1,
    )

    test_a = DiagnosticTest(
        identifier="test_a",
        outcomes=("yes", "no"),
        likelihoods={
            "worker_ok": {"yes": 0.9, "no": 0.1},
            "worker_failed": {"yes": 0.1, "no": 0.9},
        },
        expected_energy_joules=1,
        expected_latency_seconds=1,
    )

    selection = DiagnosticPolicy(method="information_gain").select(
        prior,
        [test_b, test_a],
    )

    assert selection.test.identifier == "test_a"


def test_planner_rejects_invalid_beliefs(prior, informative_test):
    with pytest.raises(ValueError):
        DiagnosticPolicy().select(
            {"worker_ok": 0.2, "worker_failed": 0.2},
            [informative_test],
        )
