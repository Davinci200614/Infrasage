"""Paired synthetic validation with separate true and assumed models.

All probabilities/costs are illustrative. No hardware energy is measured.
python -m experiments.compare_policies --suite --incidents 1000 --seed 42 --output /tmp/validation.json
"""
import argparse
from collections import Counter
from copy import deepcopy
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import random
from collections.abc import Mapping, Sequence

from backend.core.models import (Decision, DiagnosticTest, ExecutionStatus, Observation,
                                 InvestigationResult, validate_distribution)
from backend.core.orchestrator import StoppingConfig, run_investigation
from backend.diagnosis.bayesian import entropy
from backend.diagnosis.example import synthetic_model
from backend.optimization.planner import DiagnosticPolicy

SCENARIOS = ('matched', 'wrong_prior', 'zero_prior', 'likelihood_mismatch',
             'noisy', 'correlated', 'out_of_catalogue')


@dataclass(frozen=True)
class ExperimentModel:
    """True model is never passed to the inference engine or its policies."""
    name: str
    description: str
    assumed_prior: dict[str, float]
    assumed_tests: list[DiagnosticTest]
    true_prior: dict[str, float]
    # test identifier -> actual initiating cause -> outcome probabilities
    true_likelihoods: dict[str, dict[str, dict[str, float]]]
    observation_noise: float = 0.0
    shared_quantile_probability: float = 0.0


@dataclass(frozen=True)
class Incident:
    truth: str
    observations: dict[str, Observation]


def experiment_model(name: str = 'matched') -> ExperimentModel:
    prior, tests = synthetic_model()
    true_prior = dict(prior)
    true_likelihoods = {t.identifier: deepcopy(t.likelihoods) for t in tests}
    assumed_prior = dict(prior)
    noise = correlation = 0.0
    descriptions = {
        'matched': 'True prior and independent likelihoods match inference assumptions.',
        'wrong_prior': 'Assumed worker prior=.85; each other cause=.05; truth frequencies unchanged.',
        'zero_prior': 'Assumed worker prior=0; remaining masses rescaled; truth frequencies unchanged.',
        'likelihood_mismatch': 'True rows=.7*assumed row+.3*uniform; inference rows unchanged.',
        'noisy': '20% of observations replaced uniformly by a different valid outcome; inference unchanged.',
        'correlated': 'All tests share one inverse-CDF quantile per incident; true conditional marginals unchanged.',
        'out_of_catalogue': '20% novel_network_partition; remaining truth frequencies scaled by .8. Novel cause mimics redis exactly.',
    }
    if name not in descriptions:
        raise ValueError(f'Unknown scenario: {name}')
    if name == 'wrong_prior':
        assumed_prior = {h: .85 if h == 'worker_stopped' else .05 for h in prior}
    elif name == 'zero_prior':
        assumed_prior = {h: 0.0 if h == 'worker_stopped' else p / .65 for h, p in prior.items()}
    elif name == 'likelihood_mismatch':
        true_likelihoods = {t.identifier: {
            h: {o: .7 * p + .3 / len(t.outcomes) for o, p in row.items()}
            for h, row in t.likelihoods.items()} for t in tests}
    elif name == 'noisy':
        noise = .2
    elif name == 'correlated':
        correlation = 1.0
    elif name == 'out_of_catalogue':
        true_prior = {h: .8 * p for h, p in prior.items()} | {'novel_network_partition': .2}
        for t in tests:
            true_likelihoods[t.identifier]['novel_network_partition'] = dict(t.likelihoods['redis_unavailable'])
    return ExperimentModel(name, descriptions[name], assumed_prior, tests, true_prior,
                           true_likelihoods, noise, correlation)


def _draw(row: Mapping[str, float], quantile: float) -> str:
    cumulative = 0.0
    for outcome, probability in row.items():
        cumulative += probability
        if quantile < cumulative:
            return outcome
    return next(reversed(row))  # Roundoff at the upper boundary only.


def generate_incidents(model: ExperimentModel, incidents: int, seed: int) -> list[Incident]:
    """Pre-sample ALL potential outcomes, independently of policy/order/budget.

    Shared quantiles create dependence while preserving each conditional marginal.
    Noise changes outcomes after sampling and can never create execution failures.
    """
    if not isinstance(incidents, int) or incidents <= 0:
        raise ValueError('incidents must be a positive integer')
    validate_distribution(model.true_prior)
    for test in model.assumed_tests:
        if set(model.true_likelihoods[test.identifier]) != set(model.true_prior):
            raise ValueError('True model must cover every actual cause')
        for row in model.true_likelihoods[test.identifier].values():
            validate_distribution(row)
            if set(row) != set(test.outcomes):
                raise ValueError('True and assumed models must share symbolic outcomes')
    if not 0 <= model.observation_noise <= 1 or not 0 <= model.shared_quantile_probability <= 1:
        raise ValueError('Noise and correlation mixing probabilities must be in [0,1]')
    rng = random.Random(seed)
    records = []
    for _ in range(incidents):
        truth = _draw(model.true_prior, rng.random())
        shared = rng.random() < model.shared_quantile_probability
        shared_quantile = rng.random()
        observations = {}
        for test in model.assumed_tests:
            outcome = _draw(model.true_likelihoods[test.identifier][truth],
                            shared_quantile if shared else rng.random())
            if rng.random() < model.observation_noise:
                outcome = rng.choice([o for o in test.outcomes if o != outcome])
            observations[test.identifier] = Observation(test.identifier, 'synthetic-voting-app',
                ExecutionStatus.COMPLETED, outcome, test.expected_latency_seconds,
                timestamp=datetime(2000, 1, 1, tzinfo=timezone.utc))
        records.append(Incident(truth, observations))
    return records


def brier_score(probabilities: Mapping[str, float], truth: str) -> float:
    """Multiclass sum_h (p_h - 1[h=truth])**2, unscaled; range [0,2]."""
    validate_distribution(probabilities)
    if truth not in probabilities:
        raise ValueError('Map external causes to an explicit evaluation class before scoring')
    return math.fsum((p - int(h == truth)) ** 2 for h, p in probabilities.items())


def reliability_bins(probabilities: Sequence[Mapping[str, float]], truths: Sequence[str],
                     bins: int = 10) -> list[dict]:
    """Top-label calibration across ALL incidents, including abstentions.

    Equal-width [lo,hi) bins, final bin includes 1. Empty bins have null means.
    This measures top-label calibration, not full classwise calibration.
    """
    if bins <= 0 or not isinstance(bins, int) or len(probabilities) != len(truths):
        raise ValueError('Positive integer bins and aligned predictions/truths required')
    groups = [[] for _ in range(bins)]
    for p, truth in zip(probabilities, truths):
        validate_distribution(p)
        if truth not in p:
            raise ValueError('Evaluation truth must be in catalogue')
        leader = sorted(p, key=lambda h: (-p[h], h))[0]
        groups[min(int(p[leader] * bins), bins - 1)].append((p[leader], leader == truth))
    return [dict(lower=i/bins, upper=(i+1)/bins, count=len(group),
                 mean_confidence=math.fsum(p for p, _ in group)/len(group) if group else None,
                 observed_accuracy=sum(ok for _, ok in group)/len(group) if group else None)
            for i, group in enumerate(groups)]


def summarize(results: Sequence[InvestigationResult], incidents: Sequence[Incident]) -> dict:
    """Abstention is never a correct diagnosis; OOD evaluation maps to other_unknown."""
    if not results or len(results) != len(incidents):
        raise ValueError('Nonempty aligned results and incidents required')
    n = len(results)
    diagnosed = sum(r.decision == Decision.DIAGNOSED for r in results)
    correct = sum(r.diagnosed_hypothesis == i.truth for r, i in zip(results, incidents))
    energy = math.fsum(r.total_estimated_energy_joules for r in results)
    latency = math.fsum(r.total_estimated_latency_seconds for r in results)
    beliefs = [r.final_beliefs for r in results]
    evaluation_truths = [i.truth if i.truth in p else 'other_unknown' for p, i in zip(beliefs, incidents)]
    bins = reliability_bins(beliefs, evaluation_truths)
    top_correct = sum(sorted(p, key=lambda h: (-p[h], h))[0] == truth
                      for p, truth in zip(beliefs, evaluation_truths))
    ood = [(r, i) for r, i in zip(results, incidents) if i.truth not in r.final_beliefs]
    by_truth = {}
    for truth in sorted({i.truth for i in incidents}):
        group = [r for r, i in zip(results, incidents) if i.truth == truth]
        by_truth[truth] = dict(incidents=len(group), diagnosed=sum(r.decision == Decision.DIAGNOSED for r in group),
                              correct=sum(r.diagnosed_hypothesis == truth for r in group))
    return dict(incidents=n, diagnosed=diagnosed, correct=correct,
        overall_accuracy=correct/n, diagnosed_accuracy=correct/diagnosed if diagnosed else None,
        abstention_rate=1-diagnosed/n, top_label_accuracy=top_correct/n,
        mean_posterior_confidence=math.fsum(max(p.values()) for p in beliefs)/n,
        diagnosed_mean_confidence=math.fsum(max(r.final_beliefs.values()) for r in results
            if r.decision == Decision.DIAGNOSED)/diagnosed if diagnosed else None,
        mean_tests=sum(len(r.observations) for r in results)/n,
        total_estimated_energy_joules=energy, mean_energy_joules=energy/n,
        total_estimated_latency_seconds=latency, mean_latency_seconds=latency/n,
        mean_entropy_bits=math.fsum(entropy(p) for p in beliefs)/n,
        energy_per_correct=energy/correct if correct else None,
        brier_score=math.fsum(brier_score(p, truth) for p, truth in zip(beliefs, evaluation_truths))/n,
        reliability_bins=bins,
        expected_calibration_error=math.fsum(b['count']/n * abs(b['mean_confidence']-b['observed_accuracy'])
                                             for b in bins if b['count']),
        termination_reasons=dict(sorted(Counter(r.reason for r in results).items())),
        by_truth=by_truth, outside_catalogue_incidents=len(ood),
        outside_catalogue_false_diagnosis_rate=sum(r.decision == Decision.DIAGNOSED for r, _ in ood)/len(ood) if ood else None)


def evaluate(model: ExperimentModel, incidents: Sequence[Incident], stopping: StoppingConfig) -> list[dict]:
    policies = [DiagnosticPolicy('fixed', tuple(t.identifier for t in model.assumed_tests)),
                DiagnosticPolicy('information_gain'), DiagnosticPolicy('ratio'),
                DiagnosticPolicy('weighted', lambda_energy=.02, lambda_latency=.01)]
    fingerprint = hashlib.sha256(json.dumps([
        (i.truth, {k: o.observed_outcome for k, o in i.observations.items()})
        for i in incidents], sort_keys=True).encode()).hexdigest()
    rows = []
    for policy in policies:
        results = [run_investigation(model.assumed_prior, model.assumed_tests, policy,
                   predefined_observations=i.observations, stopping=stopping) for i in incidents]
        for result in results:
            if (result.decision == Decision.CONTINUE or len(result.observations) > stopping.max_tests
                or result.total_estimated_energy_joules > stopping.max_energy_joules
                or len({o.test_identifier for o in result.observations}) != len(result.observations)):
                raise AssertionError('Investigation violated termination or budget invariants')
        rows.append(dict(scenario=model.name, policy=policy.method, stopping=asdict(stopping),
                         incident_fingerprint=fingerprint, **summarize(results, incidents)))
    return rows


def compare(incidents: int = 1000, seed: int = 42, scenario: str = 'matched',
            stopping: StoppingConfig | None = None) -> list[dict]:
    model = experiment_model(scenario)
    return evaluate(model, generate_incidents(model, incidents, seed), stopping or StoppingConfig())


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--incidents', type=int, default=1000)
    parser.add_argument('--seed', type=int, default=42)
    parser.add_argument('--scenario', choices=SCENARIOS, default='matched')
    parser.add_argument('--suite', action='store_true')
    parser.add_argument('--budgets', type=float, nargs='+', default=[100.0])
    parser.add_argument('--confidence', type=float, default=.95)
    parser.add_argument('--max-tests', type=int, default=4)
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    rows, models = [], []
    for scenario in SCENARIOS if args.suite else (args.scenario,):
        model = experiment_model(scenario)
        models.append(asdict(model))
        incidents = generate_incidents(model, args.incidents, args.seed)
        for budget in args.budgets:
            rows.extend(evaluate(model, incidents, StoppingConfig(args.confidence, args.max_tests, budget)))
    report = dict(seed=args.seed, incidents_per_scenario=args.incidents, models=models, results=rows,
        definitions={
            'costs': 'Illustrative estimates only, never measured energy or latency.',
            'overall_accuracy': 'Correct known-cause diagnoses / ALL incidents; abstentions count as not correct.',
            'diagnosed_accuracy': 'Correct known-cause diagnoses / diagnosed incidents; null if none.',
            'energy_per_correct': 'Estimated joules over ALL incidents / number correctly diagnosed; null if none.',
            'brier_score': 'Mean unscaled multiclass sum of squared errors over ALL terminal posteriors.',
            'reliability': 'Top-label confidence versus catalogue top-label accuracy across ALL incidents, including abstentions.',
            'outside_catalogue': 'For Brier/reliability only, novel causes map to other_unknown; a known-cause diagnosis is always incorrect.',
            'pairing': 'Policies and budgets reuse identical pre-sampled potential test outcomes; SHA256 fingerprints included.'})
    if args.output:
        args.output.write_text(json.dumps(report, indent=2, allow_nan=False) + '\n')
    print('SYNTHETIC estimated costs only. Energy/correct includes all incident energy; n/a = zero denominator.')
    print('scenario             budget policy             overall diag-acc abstain tests total-J J/correct total-s Brier confidence ECE')
    for row in rows:
        def fmt(value):
            return 'n/a' if value is None else f'{value:.3f}'
        metrics = [row[k] for k in ('overall_accuracy', 'diagnosed_accuracy', 'abstention_rate', 'mean_tests',
                   'total_estimated_energy_joules', 'energy_per_correct', 'total_estimated_latency_seconds',
                   'brier_score', 'mean_posterior_confidence', 'expected_calibration_error')]
        print(f"{row['scenario']:21s} {row['stopping']['max_energy_joules']:5g} {row['policy']:18s} " +
              ' '.join(f'{fmt(v):>8s}' for v in metrics), flush=True)


if __name__ == '__main__':
    main()
