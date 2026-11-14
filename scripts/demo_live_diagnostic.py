#!/usr/bin/env python3
"""Read-only, explicitly targeted Docker runtime diagnostic; no planner or Bayes loop."""
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
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument('--container-name', help='Exact name of the Redis container; prefixes are not accepted')
    group.add_argument('--container-id', help='Full 64-character Redis container ID')
    parser.add_argument('--expected-outcome', choices=['running', 'stopped', 'restarting'])
    args = parser.parse_args(argv)
    target = {key: value for key, value in vars(args).items()
              if key in ('container_name', 'container_id') and value is not None}
    manager = None
    try:
        loaded = load_catalogues(research_mode=True)
        test = loaded.tests['redis_runtime_state']
        manager = CollectorManager(loaded)
        verification = manager.verify_runtime_binding(test, target)
        if verification['status'] != 'completed':
            print(json.dumps({'mode':'research execution only', 'verification':verification,
                              'result':'FAILED: Docker runtime binding was not verified'},indent=2,default=str))
            return 1
        execution = manager.execute_test(test, target)
        normalized = DiagnosticAdapter().adapt_with_metadata(test, execution, target)
        success = normalized.observation.execution_status == ExecutionStatus.COMPLETED
        if args.expected_outcome is not None:
            success = success and normalized.observation.observed_outcome == args.expected_outcome
        print(json.dumps({
            'mode':'research execution only; synthetic model, not a calibrated diagnosis',
            'target_assignment':'The operator identifies this explicit target as Redis; image/service role is not inferred',
            'test_identifier':test.identifier, 'requested_target':target,
            'verification':verification, 'execution':execution,
            'observation':asdict(normalized.observation), 'provenance':normalized.metadata,
            'model_status':loaded.metadata['model']['status'],
            'catalogue_live_availability_unchanged':loaded.test_metadata[test.identifier]['availability']['live'],
            'result':'SUCCESS: verified real Docker runtime observation' if success else 'FAILED: no validated expected observation',
        },indent=2,default=str,allow_nan=False))
        return 0 if success else 1
    except Exception as error:
        print(f'Diagnostic failed: {error}',file=sys.stderr)
        return 1
    finally:
        if manager is not None:
            manager.close()


if __name__ == '__main__':
    raise SystemExit(main())
