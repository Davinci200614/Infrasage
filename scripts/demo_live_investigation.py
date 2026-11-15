#!/usr/bin/env python3
"""Research-only live investigation using the existing lifecycle and planner."""
import argparse
from dataclasses import asdict
import json
from pathlib import Path
import sys

if __package__ in (None, ''):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.core.collector_manager import CollectorManager
from backend.core.orchestrator import LiveInvestigationConfig, StoppingConfig, run_live_investigation
from backend.diagnosis.catalogue_loader import load_catalogues
from backend.optimization.planner import DiagnosticPolicy


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    redis = parser.add_mutually_exclusive_group(required=True)
    redis.add_argument('--redis-name');redis.add_argument('--redis-id')
    worker = parser.add_mutually_exclusive_group(required=True)
    worker.add_argument('--worker-name');worker.add_argument('--worker-id')
    parser.add_argument('--redis-host')
    parser.add_argument('--redis-port', type=int)
    parser.add_argument('--helper-image')
    parser.add_argument('--runtime-only', action='store_true',
                        help='Inspect Redis state without a worker TCP probe when no endpoint is available')
    parser.add_argument('--connection-timeout', type=float, default=1.0)
    parser.add_argument('--probe-deadline', type=float, default=8.0)
    parser.add_argument('--runtime-deadline', type=float, default=3.0)
    parser.add_argument('--deadline', type=float, default=30.0)
    parser.add_argument('--max-tests', type=int, default=3)
    parser.add_argument('--max-energy', type=float, default=1.0)
    parser.add_argument('--confidence', type=float, default=.95)
    parser.add_argument('--policy', choices=['ratio', 'information_gain', 'weighted', 'fixed'], default='ratio')
    args = parser.parse_args(argv)
    if not args.runtime_only and not all((args.redis_host, args.redis_port, args.helper_image)):
        parser.error('--redis-host, --redis-port and --helper-image are required unless --runtime-only is set')
    manager = None
    try:
        loaded = load_catalogues(research_mode=True)
        manager = CollectorManager(loaded)
        config = LiveInvestigationConfig(
            redis_target={'container_id': args.redis_id} if args.redis_id else {'container_name': args.redis_name},
            worker_target={'container_id': args.worker_id} if args.worker_id else {'container_name': args.worker_name},
            probe_config=None if args.runtime_only else {'redis_host': args.redis_host, 'redis_port': args.redis_port,
                          'helper_image': args.helper_image, 'connection_timeout': args.connection_timeout,
                          'execution_deadline': args.probe_deadline},
            maximum_duration_seconds=args.deadline, runtime_deadline_seconds=args.runtime_deadline, include_worker_runtime=True)
        policy = DiagnosticPolicy(args.policy, ('redis_runtime_state', 'worker_runtime_state', 'worker_redis_connectivity'), .02, .01)
        result = run_live_investigation(loaded, manager, config, policy,
                                       StoppingConfig(confidence_threshold=args.confidence,
                                                      max_tests=args.max_tests, max_energy_joules=args.max_energy))
        print(json.dumps({'mode': 'research/bootstrap; uncalibrated probabilities and illustrative estimated costs',
                          'model': {k: loaded.metadata['model'][k] for k in ('model_id', 'version', 'status')},
                          'investigation': asdict(result)}, default=str, indent=2, allow_nan=False))
        # ABSTAIN is a valid lifecycle result; operational failures remain in trace.
        return 0
    except Exception as error:
        print(f'Investigation failed: {error}', file=sys.stderr)
        return 1
    finally:
        if manager is not None:
            manager.close()


if __name__ == '__main__':
    raise SystemExit(main())
