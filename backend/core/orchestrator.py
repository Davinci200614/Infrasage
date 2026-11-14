
"""Sequential investigation runner.

Supports three execution modes:
1. Synthetic simulation using a ground-truth hypothesis.
2. Predefined observations.
3. Collector-backed diagnostic execution.

Each test is attempted at most once.
"""

from dataclasses import dataclass
from datetime import datetime, timezone, timedelta
import random
from collections.abc import Mapping, Sequence

from backend.core.models import (
    BeliefState,
    Decision,
    DiagnosticTest,
    ExecutionStatus,
    InvestigationResult,
    Observation,
    validate_cost,
    validate_distribution,
)

from backend.diagnosis.bayesian import (
    apply_observation,
    expected_information_gain,
)

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
            raise ValueError(
                'Confidence threshold must be in (0, 1]'
            )

        if not isinstance(self.max_tests, int) or self.max_tests < 0:
            raise ValueError(
                'max_tests must be a nonnegative integer'
            )

        if (
            not isinstance(self.minimum_successful_observations, int)
            or self.minimum_successful_observations < 0
        ):
            raise ValueError(
                'minimum_successful_observations must be a '
                'nonnegative integer'
            )

        validate_cost(
            self.max_energy_joules,
            'energy budget'
        )

        validate_cost(
            self.minimum_information_gain_bits,
            'minimum information gain'
        )


def stopping_decision(
    beliefs: dict[str, float],
    remaining: Sequence[DiagnosticTest],
    attempts: int,
    successes: int,
    energy: float,
    config: StoppingConfig,
) -> tuple[Decision, str]:

    """Prototype confidence heuristic, separate from inference."""

    validate_distribution(beliefs)

    leader = sorted(
        beliefs,
        key=lambda hypothesis: (
            -beliefs[hypothesis],
            hypothesis
        )
    )[0]

    if (
        successes >= config.minimum_successful_observations
        and beliefs[leader] >= config.confidence_threshold
    ):
        if leader == 'other_unknown':
            return (
                Decision.ABSTAIN,
                'other_unknown is most probable'
            )

        return (
            Decision.DIAGNOSED,
            'posterior confidence threshold reached'
        )

    if attempts >= config.max_tests:
        return (
            Decision.ABSTAIN,
            'maximum test count reached'
        )

    if not remaining:
        return (
            Decision.ABSTAIN,
            'no remaining usable tests'
        )

    affordable = [
        test
        for test in remaining
        if test.expected_energy_joules
        <= config.max_energy_joules - energy
    ]

    if not affordable:
        return (
            Decision.ABSTAIN,
            'energy budget exhausted'
        )

    if all(
        expected_information_gain(beliefs, test)
        <= config.minimum_information_gain_bits
        for test in affordable
    ):
        return (
            Decision.ABSTAIN,
            'insufficient evidence: no informative affordable tests'
        )

    return Decision.CONTINUE, 'more evidence needed'


def _get_target_identifier(target: dict) -> str:
    """Resolve the target identifier using DiagnosticAdapter priority."""

    for key in (
        'container_name',
        'container_id',
        'service',
        'host',
        'id',
    ):
        value = target.get(key)

        if isinstance(value, str) and value.strip():
            return value.strip()

    return 'infrastructure'


def run_investigation(
    initial_beliefs: dict[str, float],
    tests: Sequence[DiagnosticTest],
    policy: DiagnosticPolicy,
    ground_truth: str | None = None,
    seed: int = 0,
    stopping: StoppingConfig | None = None,
    predefined_observations: Mapping[str, Observation] | None = None,
    target_identifier: str = 'synthetic-voting-app',
    collector_manager=None,
    diagnostic_adapter=None,
    target: dict | None = None,
) -> InvestigationResult:

    """Run a sequential diagnostic investigation.

    Modes:
    - Simulation: ground_truth is supplied.
    - Predefined: predefined_observations is supplied.
    - Collector: collector_manager, diagnostic_adapter and target
      are supplied together.

    Predefined mode is strict: missing observations raise an error.

    Collector mode executes the selected test through CollectorManager
    and converts its result through DiagnosticAdapter.

    Estimated costs count every attempt, including failed or
    unavailable executions.
    """

    validate_distribution(initial_beliefs)

    config = stopping or StoppingConfig()

    # --------------------------------------------------
    # EXECUTION MODE VALIDATION
    # --------------------------------------------------

    collector_arguments = (
        collector_manager,
        diagnostic_adapter,
        target,
    )

    collector_mode = any(
        argument is not None
        for argument in collector_arguments
    )

    if collector_mode:
        if not all(
            argument is not None
            for argument in collector_arguments
        ):
            raise ValueError(
                'Collector mode requires collector_manager, '
                'diagnostic_adapter and target'
            )

        if not isinstance(target, dict):
            raise ValueError(
                'Collector target must be a dictionary'
            )

        if ground_truth is not None:
            raise ValueError(
                'ground_truth cannot be used in collector mode'
            )

        if predefined_observations is not None:
            raise ValueError(
                'predefined_observations cannot be used '
                'in collector mode'
            )

        resolved_target_identifier = _get_target_identifier(
            target
        )

    elif predefined_observations is None:
        if ground_truth not in initial_beliefs:
            raise ValueError(
                'Simulation requires a ground-truth hypothesis '
                'in the model'
            )

        resolved_target_identifier = target_identifier

    else:
        resolved_target_identifier = target_identifier

    # --------------------------------------------------
    # TEST VALIDATION
    # --------------------------------------------------

    remaining = list(tests)

    if len({test.identifier for test in tests}) != len(tests):
        raise ValueError('Duplicate test identifiers')

    for test in tests:
        test.__post_init__()

        if set(test.likelihoods) != set(initial_beliefs):
            raise ValueError(
                'Each test must cover exactly the belief hypotheses'
            )

    # --------------------------------------------------
    # INITIAL STATE
    # --------------------------------------------------

    rng = random.Random(seed)

    beliefs = dict(initial_beliefs)

    history = [
        BeliefState(dict(beliefs))
    ]

    observations = []
    selections = []

    energy = 0.0
    latency = 0.0
    successes = 0

    # --------------------------------------------------
    # INVESTIGATION LOOP
    # --------------------------------------------------

    while True:

        decision, reason = stopping_decision(
            beliefs,
            remaining,
            len(observations),
            successes,
            energy,
            config,
        )

        if decision != Decision.CONTINUE:
            break

        affordable = [
            test
            for test in remaining
            if test.expected_energy_joules
            <= config.max_energy_joules - energy
        ]

        # 1. Planner selects the next test.
        selection = policy.select(
            beliefs,
            affordable
        )

        test = selection.test

        # --------------------------------------------------
        # MODE 1: PREDEFINED OBSERVATIONS
        # --------------------------------------------------

        if predefined_observations is not None:

            if test.identifier not in predefined_observations:
                raise ValueError(
                    f'Missing predefined observation for '
                    f'{test.identifier}'
                )

            observation = predefined_observations[
                test.identifier
            ]

            if (
                observation.target_identifier
                != resolved_target_identifier
            ):
                raise ValueError(
                    'Observation target identifier mismatch'
                )

        # --------------------------------------------------
        # MODE 2: COLLECTOR EXECUTION
        # --------------------------------------------------

        elif collector_mode:

            # Execute the selected diagnostic test.
            execution_result = collector_manager.execute_test(
                test,
                target,
            )

            # Convert raw execution result into an Observation.
            observation = diagnostic_adapter.adapt(
                test,
                execution_result,
                target,
            )

            if not isinstance(observation, Observation):
                raise TypeError(
                    'DiagnosticAdapter must return an Observation'
                )

            if observation.test_identifier != test.identifier:
                raise ValueError(
                    'Observation test identifier mismatch'
                )

            if (
                observation.target_identifier
                != resolved_target_identifier
            ):
                raise ValueError(
                    'Observation target identifier mismatch'
                )

        # --------------------------------------------------
        # MODE 3: SYNTHETIC SIMULATION
        # --------------------------------------------------

        else:

            outcome = rng.choices(
                test.outcomes,
                weights=[
                    test.likelihoods[ground_truth][outcome]
                    for outcome in test.outcomes
                ],
            )[0]

            observation = Observation(
                test.identifier,
                resolved_target_identifier,
                ExecutionStatus.COMPLETED,
                outcome,
                test.expected_latency_seconds,
                timestamp=(
                    datetime(
                        2000,
                        1,
                        1,
                        tzinfo=timezone.utc
                    )
                    + timedelta(seconds=latency)
                ),
            )

        # --------------------------------------------------
        # BAYESIAN UPDATE
        # --------------------------------------------------

        beliefs = apply_observation(
            beliefs,
            test,
            observation,
        )

        if observation.execution_status == ExecutionStatus.COMPLETED:
            successes += 1

        # --------------------------------------------------
        # ACCOUNTING
        # --------------------------------------------------

        energy += test.expected_energy_joules
        latency += test.expected_latency_seconds

        # Each test can only be attempted once.
        remaining.remove(test)

        observations.append(observation)
        selections.append(selection)

        history.append(
            BeliefState(dict(beliefs))
        )

    # --------------------------------------------------
    # FINAL RESULT
    # --------------------------------------------------

    diagnosed = (
        sorted(
            beliefs,
            key=lambda hypothesis: (
                -beliefs[hypothesis],
                hypothesis
            )
        )[0]
        if decision == Decision.DIAGNOSED
        else None
    )

    return InvestigationResult(
        decision,
        reason,
        diagnosed,
        beliefs,
        history,
        observations,
        selections,
        energy,
        latency,
    )
