"""Infrastructure-independent contracts. All model costs are estimates."""
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import StrEnum
import math
from typing import Mapping

PROBABILITY_TOLERANCE = 1e-9


def validate_distribution(values: Mapping[str, float], name: str = 'distribution') -> None:
    if not values or any(not isinstance(k, str) or not k for k in values):
        raise ValueError(f'{name} must have nonempty symbolic keys')
    if any(not math.isfinite(v) or v < 0 for v in values.values()):
        raise ValueError(f'{name} probabilities must be finite and nonnegative')
    if abs(math.fsum(values.values()) - 1) > PROBABILITY_TOLERANCE:
        raise ValueError(f'{name} probabilities must sum to one within {PROBABILITY_TOLERANCE}')


def validate_cost(value: float, name: str) -> None:
    if not math.isfinite(value) or value < 0:
        raise ValueError(f'{name} must be finite and nonnegative')


@dataclass(frozen=True)
class Hypothesis:
    identifier: str
    description: str = ''


@dataclass(frozen=True)
class DiagnosticTest:
    identifier: str
    outcomes: tuple[str, ...]
    # hypothesis -> outcome -> probability
    likelihoods: dict[str, dict[str, float]]
    expected_energy_joules: float
    expected_latency_seconds: float
    description: str = ''

    def __post_init__(self) -> None:
        if not self.identifier or len(self.outcomes) < 2 or len(set(self.outcomes)) != len(self.outcomes):
            raise ValueError('Test requires an identifier and at least two unique outcomes')
        if any(not isinstance(o, str) or not o for o in self.outcomes) or not self.likelihoods:
            raise ValueError('Test requires symbolic outcomes and hypothesis likelihoods')
        for hypothesis, row in self.likelihoods.items():
            if not hypothesis or set(row) != set(self.outcomes):
                raise ValueError('Every hypothesis must define every supported outcome')
            validate_distribution(row, f'likelihood for {hypothesis}')
        validate_cost(self.expected_energy_joules, 'energy')
        validate_cost(self.expected_latency_seconds, 'latency')


class ExecutionStatus(StrEnum):
    COMPLETED = 'completed'
    FAILED = 'failed'
    UNAVAILABLE = 'unavailable'


@dataclass(frozen=True)
class Observation:
    test_identifier: str
    target_identifier: str
    execution_status: ExecutionStatus
    observed_outcome: str | None = None
    duration_seconds: float = 0.0
    measured_energy_joules: float | None = None
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def __post_init__(self) -> None:
        if self.execution_status not in tuple(ExecutionStatus):
            raise ValueError('Invalid execution status')
        if (self.execution_status == ExecutionStatus.COMPLETED) != (self.observed_outcome is not None):
            raise ValueError('Only completed observations must contain an outcome')
        validate_cost(self.duration_seconds, 'duration')
        if self.measured_energy_joules is not None:
            validate_cost(self.measured_energy_joules, 'measured energy')


@dataclass(frozen=True)
class BeliefState:
    probabilities: dict[str, float]
    def __post_init__(self) -> None:
        validate_distribution(self.probabilities)


@dataclass(frozen=True)
class TestSelection:
    test: DiagnosticTest
    score: float | None
    information_gain_bits: float
    expected_energy_joules: float
    expected_latency_seconds: float
    policy: str
    # IG/joule is undefined at zero energy. Use explicit priority, not infinity.
    zero_cost_priority: bool = False


class Decision(StrEnum):
    CONTINUE = 'CONTINUE'
    DIAGNOSED = 'DIAGNOSED'
    ABSTAIN = 'ABSTAIN'


@dataclass
class InvestigationResult:
    decision: Decision
    reason: str
    diagnosed_hypothesis: str | None
    final_beliefs: dict[str, float]
    belief_history: list[BeliefState]
    observations: list[Observation]
    selections: list[TestSelection]
    total_estimated_energy_joules: float
    total_estimated_latency_seconds: float
