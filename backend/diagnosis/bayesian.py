"""Finite Bayesian inference, with entropy and information gain measured in bits."""
import math
from backend.core.models import DiagnosticTest, Observation, ExecutionStatus, validate_distribution


def _validate(beliefs: dict[str, float], test: DiagnosticTest) -> None:
    validate_distribution(beliefs)
    # Revalidate mutable tables at public boundaries.
    test.__post_init__()
    if set(beliefs) != set(test.likelihoods):
        raise ValueError('Beliefs and test must have exactly the same hypotheses')


def bayesian_update(prior: dict[str, float], test: DiagnosticTest,
                    observed_outcome: str) -> dict[str, float]:
    """Return P(H|o,T) using log weights to avoid product underflow."""
    _validate(prior, test)
    if observed_outcome not in test.outcomes:
        raise ValueError(f'Unsupported outcome: {observed_outcome}')
    logs = {h: math.log(p) + math.log(test.likelihoods[h][observed_outcome])
            for h, p in prior.items() if p > 0 and test.likelihoods[h][observed_outcome] > 0}
    if not logs:
        raise ValueError('Observation has zero predictive probability')
    maximum = max(logs.values())
    weights = {h: math.exp(logs[h] - maximum) if h in logs else 0.0 for h in prior}
    total = math.fsum(weights.values())
    return {h: w / total for h, w in weights.items()}


def apply_observation(prior: dict[str, float], test: DiagnosticTest,
                      observation: Observation) -> dict[str, float]:
    """Execution failures/unavailability carry no diagnostic evidence."""
    _validate(prior, test)
    if observation.test_identifier != test.identifier:
        raise ValueError('Observation test identifier mismatch')
    if observation.execution_status != ExecutionStatus.COMPLETED:
        return dict(prior)
    return bayesian_update(prior, test, observation.observed_outcome)


def entropy(beliefs: dict[str, float]) -> float:
    """Shannon entropy: -sum p log2 p, with 0 log 0 = 0."""
    validate_distribution(beliefs)
    return -math.fsum(p * math.log2(p) for p in beliefs.values() if p > 0)


def expected_information_gain(beliefs: dict[str, float], test: DiagnosticTest) -> float:
    """H(b) - sum_o P(o|b,T) H(b^{T,o})."""
    _validate(beliefs, test)
    expected = []
    for outcome in test.outcomes:
        probability = math.fsum(p * test.likelihoods[h][outcome] for h, p in beliefs.items())
        if probability > 0:
            expected.append(probability * entropy(bayesian_update(beliefs, test, outcome)))
    gain = entropy(beliefs) - math.fsum(expected)
    if gain < -1e-9:
        raise ArithmeticError(f'Substantially negative information gain: {gain}')
    return max(0.0, gain)
