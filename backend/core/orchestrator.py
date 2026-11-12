"""Sequential runner independent of Docker. Each test is attempted at most once."""
from dataclasses import dataclass
from datetime import datetime, timezone, timedelta
import random
from collections.abc import Mapping, Sequence
from backend.core.models import (BeliefState, Decision, DiagnosticTest, ExecutionStatus,
                                 InvestigationResult, Observation, validate_cost, validate_distribution)
from backend.diagnosis.bayesian import apply_observation, expected_information_gain
from backend.optimization.planner import DiagnosticPolicy


@dataclass(frozen=True)
class StoppingConfig:
    confidence_threshold: float = 0.95
    max_tests: int = 4
    max_energy_joules: float = 100.0
    minimum_information_gain_bits: float = 1e-12
    minimum_successful_observations: int = 1

    def __post_init__(self) -> None:
        if not 0 < self.confidence_threshold <= 1:
            raise ValueError('Confidence threshold must be in (0, 1]')
        if not isinstance(self.max_tests, int) or self.max_tests < 0:
            raise ValueError('max_tests must be a nonnegative integer')
        if not isinstance(self.minimum_successful_observations, int) or self.minimum_successful_observations < 0:
            raise ValueError('minimum_successful_observations must be a nonnegative integer')
        validate_cost(self.max_energy_joules, 'energy budget')
        validate_cost(self.minimum_information_gain_bits, 'minimum information gain')


def stopping_decision(beliefs: dict[str, float], remaining: Sequence[DiagnosticTest],
                      attempts: int, successes: int, energy: float,
                      config: StoppingConfig) -> tuple[Decision, str]:
    """Prototype confidence heuristic, separate from inference; confidence is not accuracy."""
    validate_distribution(beliefs)
    leader = sorted(beliefs, key=lambda h: (-beliefs[h], h))[0]
    if successes >= config.minimum_successful_observations and beliefs[leader] >= config.confidence_threshold:
        if leader == 'other_unknown':
            return Decision.ABSTAIN, 'other_unknown is most probable'
        return Decision.DIAGNOSED, 'posterior confidence threshold reached'
    if attempts >= config.max_tests:
        return Decision.ABSTAIN, 'maximum test count reached'
    if not remaining:
        return Decision.ABSTAIN, 'no remaining usable tests'
    affordable = [t for t in remaining if t.expected_energy_joules <= config.max_energy_joules - energy]
    if not affordable:
        return Decision.ABSTAIN, 'energy budget exhausted'
    if all(expected_information_gain(beliefs, t) <= config.minimum_information_gain_bits for t in affordable):
        return Decision.ABSTAIN, 'insufficient evidence: no informative affordable tests'
    return Decision.CONTINUE, 'more evidence needed'


def run_investigation(initial_beliefs: dict[str, float], tests: Sequence[DiagnosticTest],
                      policy: DiagnosticPolicy, ground_truth: str | None = None,
                      seed: int = 0, stopping: StoppingConfig | None = None,
                      predefined_observations: Mapping[str, Observation] | None = None,
                      target_identifier: str = 'synthetic-voting-app') -> InvestigationResult:
    """Sample conditional outcomes, or consume one supplied Observation per selected test.

    Predefined mode is strict: missing observations raise an error. No collector is called.
    Estimated costs count every attempt, including failed/unavailable executions.
    """
    validate_distribution(initial_beliefs)
    config = stopping or StoppingConfig()
    remaining = list(tests)
    if len({t.identifier for t in tests}) != len(tests):
        raise ValueError('Duplicate test identifiers')
    for test in tests:
        test.__post_init__()
        if set(test.likelihoods) != set(initial_beliefs):
            raise ValueError('Each test must cover exactly the belief hypotheses')
    if predefined_observations is None and ground_truth not in initial_beliefs:
        raise ValueError('Simulation requires a ground-truth hypothesis in the model')
    rng = random.Random(seed)
    beliefs = dict(initial_beliefs)
    history = [BeliefState(dict(beliefs))]
    observations, selections = [], []
    energy = latency = 0.0
    successes = 0
    while True:
        decision, reason = stopping_decision(beliefs, remaining, len(observations), successes, energy, config)
        if decision != Decision.CONTINUE:
            break
        affordable = [t for t in remaining if t.expected_energy_joules <= config.max_energy_joules - energy]
        selection = policy.select(beliefs, affordable)
        test = selection.test
        if predefined_observations is not None:
            if test.identifier not in predefined_observations:
                raise ValueError(f'Missing predefined observation for {test.identifier}')
            observation = predefined_observations[test.identifier]
            if observation.target_identifier != target_identifier:
                raise ValueError('Observation target identifier mismatch')
        else:
            outcome = rng.choices(test.outcomes, weights=[test.likelihoods[ground_truth][o] for o in test.outcomes])[0]
            observation = Observation(test.identifier, target_identifier, ExecutionStatus.COMPLETED,
                                      outcome, test.expected_latency_seconds,
                                      timestamp=datetime(2000, 1, 1, tzinfo=timezone.utc) + timedelta(seconds=latency))
        beliefs = apply_observation(beliefs, test, observation)
        successes += observation.execution_status == ExecutionStatus.COMPLETED
        energy += test.expected_energy_joules
        latency += test.expected_latency_seconds
        remaining.remove(test)
        observations.append(observation)
        selections.append(selection)
        history.append(BeliefState(dict(beliefs)))
    # Match stopping_decision's deterministic tie-breaking, including unknown ties.
    diagnosed = sorted(beliefs, key=lambda h: (-beliefs[h], h))[0] if decision == Decision.DIAGNOSED else None
    return InvestigationResult(decision, reason, diagnosed, beliefs, history, observations,
                               selections, energy, latency)
