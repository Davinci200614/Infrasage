"""Deterministic greedy policies; no claim of global energy optimality."""
from dataclasses import dataclass
from typing import Sequence
from backend.core.models import DiagnosticTest, TestSelection, validate_cost
from backend.diagnosis.bayesian import expected_information_gain


@dataclass(frozen=True)
class DiagnosticPolicy:
    method: str = 'information_gain'
    fixed_order: tuple[str, ...] = ()
    lambda_energy: float = 0.0  # bits / joule
    lambda_latency: float = 0.0  # bits / second

    def __post_init__(self) -> None:
        if self.method not in ('fixed', 'information_gain', 'ratio', 'weighted'):
            raise ValueError('Unknown policy method')
        if len(set(self.fixed_order)) != len(self.fixed_order):
            raise ValueError('Fixed order contains duplicate tests')
        validate_cost(self.lambda_energy, 'lambda_energy')
        validate_cost(self.lambda_latency, 'lambda_latency')

    def select(self, beliefs: dict[str, float], tests: Sequence[DiagnosticTest]) -> TestSelection:
        if not tests:
            raise ValueError('No available tests')
        if len({t.identifier for t in tests}) != len(tests):
            raise ValueError('Duplicate test identifiers')
        candidates = []
        for test in tests:
            gain = expected_information_gain(beliefs, test)
            zero_cost_priority = False
            if self.method == 'fixed':
                if test.identifier not in self.fixed_order:
                    raise ValueError('Fixed order must include every available test')
                score = -float(self.fixed_order.index(test.identifier))
            elif self.method == 'information_gain':
                score = gain
            elif self.method == 'ratio':
                # Free informative tests have priority; free uninformative tests score zero.
                zero_cost_priority = test.expected_energy_joules == 0 and gain > 0
                score = None if zero_cost_priority else (
                    gain / test.expected_energy_joules if test.expected_energy_joules else 0.0)
            else:
                score = gain - self.lambda_energy * test.expected_energy_joules - self.lambda_latency * test.expected_latency_seconds
            candidates.append(TestSelection(test, score, gain, test.expected_energy_joules,
                                            test.expected_latency_seconds, self.method,
                                            zero_cost_priority))
        return sorted(candidates, key=lambda c: (
            not c.zero_cost_priority, -(c.score or 0.0), c.test.identifier))[0]
