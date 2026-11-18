#!/usr/bin/env python3
"""Explicit worker-network TCP diagnostic; no planner or investigation lifecycle."""
import argparse
from dataclasses import asdict
import json
from pathlib import Path
import sys

if __package__ in (None, ''):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.core.collector_manager import CollectorManager
from backend.core.diagnostic_adapter import DiagnosticAdapter
from backend.core.models import ExecutionStatus
from backend.diagnosis.catalogue_loader import load_catalogues


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    identity = parser.add_mutually_exclusive_group(required=True)
    identity.add_argument('--worker-name')
    identity.add_argument('--worker-id')
    parser.add_argument('--redis-host', required=True)
    parser.add_argument('--redis-port', type=int, required=True)
    parser.add_argument('--helper-image', required=True, help='Full local sha256 image ID from build_tcp_probe_image.py')
    parser.add_argument('--connection-timeout', type=float, default=1.0)
    parser.add_argument('--execution-deadline', type=float, default=8.0)
    args = parser.parse_args(argv)
    target = ({'container_id': args.worker_id} if args.worker_id is not None
              else {'container_name': args.worker_name})
    manager = None
    try:
        loaded = load_catalogues(research_mode=True)
        test = loaded.tests['worker_redis_connectivity']
        manager = CollectorManager(loaded)
        verification = manager.verify_connectivity_binding(
            test, target, redis_host=args.redis_host, redis_port=args.redis_port,
            connection_timeout=args.connection_timeout, execution_deadline=args.execution_deadline,
            helper_image=args.helper_image)
        execution = (manager.execute_test(test, target) if verification.get('status') == 'completed'
                     else verification)
        normalized = DiagnosticAdapter().adapt_with_metadata(test, execution, target)
        success = normalized.observation.execution_status == ExecutionStatus.COMPLETED
        print(json.dumps({'mode': 'research execution; TCP reachability only, not Redis application health',
                          'worker_target': target, 'redis_endpoint': {'host': args.redis_host, 'port': args.redis_port},
                          'verification': verification, 'execution': execution,
                          'observation': asdict(normalized.observation), 'provenance': normalized.metadata,
                          'model_status': loaded.metadata['model']['status'],
                          'result': 'SUCCESS: worker-context TCP observation' if success else 'FAILED/UNAVAILABLE: see execution error'},
                         indent=2, default=str, allow_nan=False))
        return 0 if success else 1
    except Exception as error:
        print(f'Connectivity diagnostic failed: {error}', file=sys.stderr)
        return 1
    finally:
        if manager is not None:
            manager.close()


if __name__ == '__main__':
    raise SystemExit(main())
