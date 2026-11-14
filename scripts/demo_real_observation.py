#!/usr/bin/env python3
"""One real observation → existing Bayesian update. No planning or orchestration."""
import argparse
from dataclasses import asdict
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
from time import perf_counter

# Support the requested `python scripts/demo_real_observation.py` invocation.
if __package__ in (None, ''):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.collectors.netdata import NetdataCollector
from backend.core.models import ExecutionStatus, validate_distribution
from backend.diagnosis.bayesian import apply_observation
from backend.diagnosis.catalogue_loader import CatalogueError, load_catalogues
from backend.processing.normalizer import normalize_cpu_observation


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--base-url', default='http://127.0.0.1:19999')
    parser.add_argument('--target', default='linux-host-01', help='User-supplied target label, not discovered identity')
    args = parser.parse_args(argv)
    collector = None
    try:
        loaded = load_catalogues(research_mode=True)
        if not loaded.test_metadata['host_cpu_pressure']['availability']['simulation']:
            raise PermissionError('CPU test is not available for research/bootstrap inference')
        collector = NetdataCollector(args.base_url)
        start = perf_counter()
        try:
            snapshot = collector.collect()
        except Exception as error:
            # A provider-level exception is execution failure, never a fault outcome.
            snapshot = {'source': 'netdata', 'cpu': None, 'errors': {'cpu': str(error)}}
        duration = perf_counter() - start
        test = loaded.tests['host_cpu_pressure']
        normalized = normalize_cpu_observation(snapshot, test, loaded.test_metadata[test.identifier],
                                               args.target, retrieved_at=datetime.now(timezone.utc),
                                               duration_seconds=duration)
        posterior = apply_observation(loaded.initial_prior, test, normalized.observation)
        validate_distribution(posterior, 'posterior')
        completed = normalized.observation.execution_status == ExecutionStatus.COMPLETED
        model = loaded.metadata['model']
        output = {
            'mode': 'research/bootstrap inference, not a calibrated diagnosis',
            'netdata_url': args.base_url,
            'netdata_connection_status': 'CPU evidence received' if completed else 'CPU evidence unavailable or failed; see reason/errors',
            'target_identifier': args.target, 'target_identity_source': 'CLI label',
            'monitoring': normalized.metadata,
            'observation': asdict(normalized.observation),
            'model': {k: model[k] for k in ('model_id', 'version', 'status', 'usage_policy')},
            'catalogue_versions': {k: v['version'] for k, v in loaded.metadata.items()},
            'live_execution_enabled': False,
            'initial_prior': loaded.initial_prior, 'updated_posterior': posterior,
            'posterior_sum': sum(posterior.values()),
            'result': ('SUCCESS: real CPU observation processed by the existing Bayesian engine'
                       if completed else 'FAILED: no usable CPU evidence; no diagnosis or synthetic fallback'),
        }
        print(json.dumps(output, default=str, indent=2, allow_nan=False))
        return 0 if completed else 1
    except (CatalogueError, PermissionError, ValueError) as error:
        print(f'Integration failed: {error}', file=sys.stderr)
        return 1
    finally:
        if collector is not None:
            collector.session.close()


if __name__ == '__main__':
    raise SystemExit(main())
