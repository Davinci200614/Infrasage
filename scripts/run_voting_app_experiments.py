#!/usr/bin/env python3
"""Opt-in faults on a manifest- and label-verified isolated Voting App only."""
import argparse
import json
from pathlib import Path
import subprocess
import sys

if __package__ in (None,''):
    sys.path.insert(0,str(Path(__file__).resolve().parents[1]))

import docker
from backend.collectors.tcp_probe import helper_source_sha
from lab.voting_app import SCENARIOS,VotingAppLab

ROOT = Path(__file__).resolve().parents[1]


def investigate_via_launcher(redis_id, worker_id, redis_ip):
    """Exercise the public entry point; do not pass the pre-fault endpoint to it."""
    completed = subprocess.run([str(ROOT / 'infrasage.sh'), 'investigate'],
                               cwd=ROOT, text=True, capture_output=True, timeout=45)
    if completed.returncode:
        raise RuntimeError('Public launcher failed: ' + completed.stderr.strip())
    payload = json.loads(completed.stdout[completed.stdout.index('{'):])
    return {'model': payload['model'], 'investigation': payload['investigation'],
            'launcher': {'exit_code': completed.returncode, 'stdout': completed.stdout,
                         'stderr': completed.stderr}}


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--manifest',type=Path,default=Path('lab/voting-app-manifest.json'))
    p.add_argument('--helper-image',required=True)
    p.add_argument('--scenario',choices=[*SCENARIOS,'all'],default='healthy')
    p.add_argument('--allow-fault-injection',action='store_true')
    p.add_argument('--restore-only',action='store_true',help='Restore only the manifest-owned lab and verify a full vote')
    p.add_argument('--output',type=Path,default=Path('experiments/data/phase2c'))
    p.add_argument('--via-launcher', action='store_true',
                   help='Test ./infrasage.sh investigate rather than calling the engine directly')
    args=p.parse_args(argv)
    if args.via_launcher and args.manifest.resolve() != ROOT / 'lab/voting-app-manifest.json':
        p.error('--via-launcher uses the default lab manifest only')
    if (args.scenario!='healthy' or args.restore_only) and not args.allow_fault_injection:
        p.error('Fault injection/restoration requires --allow-fault-injection')
    client=docker.from_env(timeout=10)
    try:
        helper_id = args.helper_image
        if not args.restore_only:
            helper = client.images.get(args.helper_image)
            labels = helper.attrs.get('Config', {}).get('Labels') or {}
            if (labels.get('org.infrasage.tcp-probe') != 'v1'
                    or labels.get('org.infrasage.tcp-probe.sha256') != helper_source_sha()):
                raise RuntimeError('Missing or outdated TCP probe build; run ./infrasage.sh setup')
            helper_id = helper.id
        lab=VotingAppLab(client,json.loads(args.manifest.read_text()),helper_id,args.output,
                         investigator=investigate_via_launcher if args.via_launcher else None)
        if args.restore_only:
            before=lab.snapshot()
            print(json.dumps(lab.restore(before,restart_worker=True),indent=2,default=str))
            return 0
        for scenario in SCENARIOS if args.scenario=='all' else [args.scenario]:
            result=lab.run_scenario(scenario,allow_fault_injection=args.allow_fault_injection)
            print(json.dumps({'scenario':scenario,'artifact':result['artifact_path'],'evaluation':result.get('evaluation'),
                              'experiment_error':result.get('experiment_error'),
                              'restoration_success':result['restoration']['success'],
                              'restoration_error':result['restoration'].get('error')},indent=2),flush=True)
            if not result['successful_experiment']:
                return 1  # Never inject another fault after failed restoration.
        return 0
    finally:client.close()


if __name__=='__main__':raise SystemExit(main())
