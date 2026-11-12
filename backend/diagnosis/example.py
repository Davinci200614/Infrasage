"""Hypothetical Voting App model; no empirical calibration or measured costs."""
from backend.core.models import DiagnosticTest


def synthetic_model() -> tuple[dict[str, float], list[DiagnosticTest]]:
    prior = dict(worker_stopped=0.35, redis_unavailable=0.30,
                 database_unavailable=0.25, other_unknown=0.10)
    specifications = [
        ('check_worker_state', ('stopped', 'running'), (0.98, 0.03, 0.03, 0.15), 1.0, 0.1),
        ('check_redis_connectivity', ('unreachable', 'reachable'), (0.02, 0.97, 0.02, 0.15), 2.0, 0.3),
        ('check_database_connectivity', ('unreachable', 'reachable'), (0.02, 0.02, 0.97, 0.15), 3.0, 0.5),
        ('check_application_workflow', ('worker_failure', 'redis_failure', 'database_failure', 'unclassified'),
         None, 12.0, 2.0),
    ]
    tests = []
    for identifier, outcomes, probabilities, energy, latency in specifications:
        if probabilities is None:
            rows = {h: {o: 0.91 if i == j else 0.03 for j, o in enumerate(outcomes)}
                    for i, h in enumerate(prior)}
        else:
            rows = {h: {outcomes[0]: p, outcomes[1]: 1 - p}
                    for h, p in zip(prior, probabilities)}
        tests.append(DiagnosticTest(identifier, outcomes, rows, energy, latency,
                                    'Illustrative assumptions, not calibrated probabilities or measured costs'))
    return prior, tests


if __name__ == '__main__':
    from dataclasses import asdict
    import json
    from backend.core.orchestrator import run_investigation
    from backend.optimization.planner import DiagnosticPolicy
    prior, tests = synthetic_model()
    result = run_investigation(prior, tests, DiagnosticPolicy('weighted', lambda_energy=0.02),
                               ground_truth='redis_unavailable', seed=7)
    print(json.dumps(asdict(result), default=str, indent=2))
