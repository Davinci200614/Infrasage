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


# Live execution is an additive path in the SAME lifecycle module. The runner,
# stopping rules and mathematical APIs above remain unchanged.
@dataclass(frozen=True)
class LiveInvestigationConfig:
    redis_target: dict | None = None
    worker_target: dict | None = None
    probe_config: dict | None = None
    maximum_duration_seconds: float = 30.0
    runtime_deadline_seconds: float = 3.0

    def __post_init__(self):
        for name in ('maximum_duration_seconds', 'runtime_deadline_seconds'):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise ValueError(f'{name} must be numeric')
            validate_cost(value, name)
        if not .1 <= self.runtime_deadline_seconds <= 10:
            raise ValueError('Runtime diagnostic deadline must be between .1 and 10 seconds')


@dataclass
class LiveInvestigationResult:
    result: InvestigationResult
    trace: list[dict]
    evidence_records: list
    rejected_evidence: list[dict]
    elapsed_seconds: float


def run_live_investigation(catalogues, manager, configuration: LiveInvestigationConfig,
                           policy: DiagnosticPolicy | None = None,
                           stopping: StoppingConfig | None = None,
                           initial_evidence: Sequence = (),
                           energy_measure=None) -> LiveInvestigationResult:
    """Plan, execute and admit each supported diagnostic at most once.

    Capability checks are pure configuration checks. Selected binding verification
    is the ONE charged diagnostic action and supplies its evidence: no subsequent
    execute_test call duplicates it. Initial records must carry raw Phase 2A/2B.1
    provenance and are revalidated; accepted unique records consume an attempt and
    their estimated cost. Rejected initial records do not consume new work.

    If ``energy_measure`` is provided, it is called as ``energy_measure(tid, func)``
    where ``func`` executes the test and returns raw evidence. The callback should
    measure energy and return (raw_evidence, energy_joules, energy_method).
    """
    """Plan, execute and admit each supported diagnostic at most once.

    Capability checks are pure configuration checks. Selected binding verification
    is the ONE charged diagnostic action and supplies its evidence: no subsequent
    execute_test call duplicates it. Initial records must carry raw Phase 2A/2B.1
    provenance and are revalidated; accepted unique records consume an attempt and
    their estimated cost. Rejected initial records do not consume new work.
    """
    from dataclasses import replace
    import time
    from backend.core.diagnostic_adapter import DiagnosticAdapter
    from backend.diagnosis.catalogue_loader import LoadedCatalogues
    from backend.processing.normalizer import NormalizedObservation

    if not isinstance(catalogues, LoadedCatalogues) or not catalogues.research_mode:
        raise ValueError('Live investigations require explicit research catalogues')
    if manager.catalogue is not catalogues:
        raise ValueError('Manager must use the same loaded catalogue instance')
    if not isinstance(configuration, LiveInvestigationConfig):
        raise TypeError('configuration must be LiveInvestigationConfig')
    config = stopping or StoppingConfig()
    selection_policy = policy or DiagnosticPolicy('ratio')
    supported = ('redis_runtime_state', 'worker_redis_connectivity', 'worker_runtime_state')
    tests = {tid: catalogues.tests[tid] for tid in supported if tid in catalogues.tests}
    beliefs = dict(catalogues.initial_prior)
    validate_distribution(beliefs)
    for test in tests.values():
        test.__post_init__()
        if set(test.likelihoods) != set(beliefs):
            raise ValueError('Test hypotheses differ from prior')
    started = time.monotonic()
    deadline = started + configuration.maximum_duration_seconds
    trace, evidence, rejected = [], [], []
    observations, selections = [], []
    history = [BeliefState(dict(beliefs))]
    consumed, groups, measurements = set(), set(), set()
    energy = latency = 0.0
    successes = 0
    adapter = DiagnosticAdapter()

    def event(kind, **details):
        trace.append({'event': kind, 'timestamp': datetime.now(timezone.utc).isoformat(),
                      'elapsed_seconds': time.monotonic() - started, **details})

    def target_for(tid):
        if tid == 'redis_runtime_state':
            return configuration.redis_target
        return configuration.worker_target

    def action_deadline(tid):
        if tid == 'redis_runtime_state':
            return configuration.runtime_deadline_seconds
        probe = configuration.probe_config
        return probe.get('execution_deadline') if isinstance(probe, dict) else None

    def evidence_group(tid):
        group = catalogues.test_metadata.get(tid, {}).get('evidence_group')
        return group if isinstance(group, str) and group else None

    def capability(tid):
        return manager.check_test_capability(tests[tid], target_for(tid),
                                             probe_config=configuration.probe_config,
                                             deadline_seconds=action_deadline(tid))

    def normalize(tid, raw, measured_energy_joules=None, energy_method="not_measured"):
        try:
            normalized = adapter.adapt_with_metadata(tests[tid], raw, target_for(tid))
            # Add measured energy to the observation if provided
            if measured_energy_joules is not None:
                obs = normalized.observation
                # Create new observation with energy fields (Observation is frozen)
                from dataclasses import replace
                new_obs = replace(obs, measured_energy_joules=measured_energy_joules, energy_method=energy_method)
                return NormalizedObservation(new_obs, normalized.metadata)
            return normalized
        except Exception as error:
            target = target_for(tid) or {}
            label = ('docker:' + str(target['container_id']) if target.get('container_id')
                     else 'docker-name:' + str(target.get('container_name', 'unresolved')))
            obs = Observation(tid, label, ExecutionStatus.FAILED)
            return NormalizedObservation(obs, {'reason': str(error), 'execution_result': raw,
                                              'observation_timestamp_role': 'processing_attempt'})

    def validate_evidence(tid, normalized):
        obs, metadata = normalized.observation, normalized.metadata
        raw = metadata.get('execution_result')
        if obs.execution_status != ExecutionStatus.COMPLETED:
            return False, metadata.get('reason', 'execution unsuccessful'), None
        try:
            obs.__post_init__()
            target = target_for(tid)
            identity = raw['target']
            if (obs.test_identifier != tid or raw['test_id'] != tid
                    or raw.get('status') != 'completed' or metadata.get('identity_verified') is not True
                    or not manager._target_matches(target, identity)
                    or obs.target_identifier != 'docker:' + identity['container_id']
                    or obs.observed_outcome not in tests[tid].outcomes
                    or obs.timestamp != datetime.fromisoformat(raw['observed_at'])):
                return False, 'evidence identity or vocabulary mismatch', None
            age = (datetime.now(timezone.utc) - obs.timestamp).total_seconds()
            if obs.timestamp.tzinfo is None or not 0 <= age <= 15:
                return False, 'stale or invalid evidence timestamp', None
            if time.monotonic() > deadline:
                return False, 'investigation deadline exceeded before evidence admission', None
            # Exclude test ID and outcome: relabeling the same underlying sample
            # must not turn it into independent evidence.
            key = (raw['source'], obs.target_identifier, obs.timestamp.isoformat())
            if key in measurements:
                return False, 'duplicate underlying measurement', None
            group = evidence_group(tid)
            if group is None or group in groups:
                return False, 'missing or already consumed evidence group', None
            return True, 'validated unique evidence', key
        except (KeyError, TypeError, ValueError, AttributeError):
            return False, 'malformed observation or provenance', None

    def account(tid, normalized, *, selected):
        nonlocal beliefs, energy, latency, successes
        obs = normalized.observation
        valid, reason, key = validate_evidence(tid, normalized)
        if valid:
            try:
                updated = apply_observation(beliefs, tests[tid], obs)
                validate_distribution(updated)
            except (ValueError, ArithmeticError) as error:
                valid, reason = False, f'evidence rejected by Bayesian engine: {error}'
        if not selected and not valid:
            rejected.append({'test_id': tid, 'reason': reason, 'evidence': normalized})
            event('initial_evidence_rejected', test_id=tid, reason=reason)
            return False
        consumed.add(tid)
        energy += tests[tid].expected_energy_joules
        latency += tests[tid].expected_latency_seconds
        if valid:
            beliefs = updated
            successes += 1
            groups.add(evidence_group(tid))
            measurements.add(key)
            evidence.append(normalized)
        else:
            if obs.execution_status == ExecutionStatus.COMPLETED:
                obs = replace(obs, execution_status=ExecutionStatus.FAILED, observed_outcome=None)
            rejected.append({'test_id': tid, 'reason': reason, 'evidence': normalized})
        observations.append(obs)
        history.append(BeliefState(dict(beliefs)))
        event('observation', test_id=tid, observation=obs, admitted=valid, reason=reason,
              provenance=normalized.metadata)
        event('posterior', probabilities=dict(beliefs), attempted_tests=[o.test_identifier for o in observations],
              completed_tests=[e.observation.test_identifier for e in evidence],
              remaining_candidates=[t for t in tests if t not in consumed],
              total_estimated_energy_joules=energy, total_estimated_latency_seconds=latency)
        return valid

    event('started', initial_probabilities=dict(beliefs),
          mode='research/bootstrap: provisional probabilities and estimated costs, not calibrated diagnosis',
          conditional_independence_assumption='Distinct admitted evidence groups are still assumed conditionally independent given cause')
    for supplied in initial_evidence:
        if (not isinstance(supplied, NormalizedObservation)
                or not isinstance(supplied.observation, Observation)
                or not isinstance(supplied.metadata, dict)
                or not isinstance(supplied.observation.test_identifier, str)):
            rejected.append({'test_id': None, 'reason': 'initial evidence requires NormalizedObservation provenance'})
            event('initial_evidence_rejected', reason='initial evidence requires NormalizedObservation provenance')
            continue
        tid = supplied.observation.test_identifier
        reason = None
        if tid not in tests:
            reason = 'initial test is not supported by this live investigation'
        elif tid in consumed:
            reason = 'duplicate initial test already consumed'
        elif not capability(tid)['eligible']:
            reason = 'initial evidence has no explicit supported target/configuration'
        elif len(observations) >= config.max_tests or energy + tests[tid].expected_energy_joules > config.max_energy_joules:
            reason = 'initial evidence exceeds investigation attempt/energy budget'
        if reason:
            rejected.append({'test_id': tid, 'reason': reason})
            event('initial_evidence_rejected', test_id=tid, reason=reason)
            continue
        raw = supplied.metadata.get('execution_result')
        normalized = normalize(tid, raw)
        # A caller cannot change an outcome/identity while retaining valid raw evidence.
        old, new = supplied.observation, normalized.observation
        if (old.test_identifier, old.target_identifier, old.execution_status, old.observed_outcome, old.timestamp) != (
                new.test_identifier, new.target_identifier, new.execution_status, new.observed_outcome, new.timestamp):
            rejected.append({'test_id': tid, 'reason': 'initial observation differs from revalidated raw evidence'})
            event('initial_evidence_rejected', test_id=tid, reason='initial observation differs from revalidated raw evidence')
            continue
        account(tid, normalized, selected=False)

    while True:
        eligible, excluded = [], {}
        remaining_seconds = deadline - time.monotonic()
        for tid, test in tests.items():
            if tid in consumed:
                excluded[tid] = 'already attempted or consumed as initial evidence'
            elif evidence_group(tid) is None or evidence_group(tid) in groups:
                excluded[tid] = 'missing or consumed evidence group'
            else:
                check = capability(tid)
                if not check['eligible']:
                    excluded[tid] = check['reason']
                elif remaining_seconds < action_deadline(tid):
                    excluded[tid] = 'insufficient remaining investigation deadline'
                else:
                    eligible.append(test)
        event('eligibility', eligible_tests=[t.identifier for t in eligible], excluded_tests=excluded,
              affordable_tests=[t.identifier for t in eligible
                                if t.expected_energy_joules <= config.max_energy_joules - energy],
              remaining_estimated_energy_joules=max(0, config.max_energy_joules - energy),
              remaining_seconds=max(0, remaining_seconds))
        decision, reason = stopping_decision(beliefs, eligible, len(observations), successes, energy, config)
        if decision == Decision.CONTINUE and remaining_seconds <= 0:
            decision, reason = Decision.ABSTAIN, 'overall investigation execution deadline exhausted'
        elif decision == Decision.ABSTAIN and reason == 'no remaining usable tests' and any(
                'deadline' in r for r in excluded.values()):
            reason = 'no eligible tests fit the remaining investigation deadline'
        if decision != Decision.CONTINUE:
            break
        affordable = [t for t in eligible if t.expected_energy_joules <= config.max_energy_joules - energy]
        selection = selection_policy.select(beliefs, affordable)
        test = selection.test
        tid = test.identifier
        selections.append(selection)
        event('selected', test_id=tid, information_gain_bits=selection.information_gain_bits,
              policy=selection.policy, policy_score=selection.score,
              expected_energy_joules=selection.expected_energy_joules,
              expected_latency_seconds=selection.expected_latency_seconds, target=target_for(tid))
        test_energy_joules = None
        test_energy_method = "not_measured"
        try:
            if energy_measure:
                raw, test_energy_joules, test_energy_method = energy_measure(
                    tid,
                    lambda: manager.execute_investigation_test(
                        test, target_for(tid),
                        probe_config=configuration.probe_config,
                        deadline_seconds=min(action_deadline(tid),
                                             max(0, deadline - time.monotonic()))
                    )
                )
            else:
                raw = manager.execute_investigation_test(test, target_for(tid),
                                                         probe_config=configuration.probe_config,
                                                         deadline_seconds=min(action_deadline(tid),
                                                                              max(0, deadline - time.monotonic())))
        except Exception as error:
            raw = {'test_id': tid, 'status': 'failed', 'error': str(error)}
        event('action', test_id=tid, execution_status=raw.get('status') if isinstance(raw, dict) else 'failed',
              phase=raw.get('action_phase') if isinstance(raw, dict) else 'malformed_result',
              accounting='one charged attempt; verification evidence consumed without another execution')
        # Pass measured energy to normalize for inclusion in frozen observation
        normalized = normalize(tid, raw, test_energy_joules, test_energy_method)
        account(tid, normalized, selected=True)

    leader = sorted(beliefs, key=lambda h: (-beliefs[h], h))[0] if decision == Decision.DIAGNOSED else None
    result = InvestigationResult(decision, reason, leader, beliefs, history, observations, selections, energy, latency)
    event('stopped', decision=decision, reason=reason, diagnosed_hypothesis=leader,
          final_probabilities=dict(beliefs), selected_tests=[s.test.identifier for s in selections],
          attempted_tests=[o.test_identifier for o in observations],
          completed_tests=[e.observation.test_identifier for e in evidence])
    return LiveInvestigationResult(result, trace, evidence, rejected, time.monotonic() - started)
