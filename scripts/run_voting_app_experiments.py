#!/usr/bin/env python3
"""Opt-in faults on a manifest- and label-verified isolated Voting App only."""
import argparse
import json
from pathlib import Path
import sys

if __package__ in (None,''):
    sys.path.insert(0,str(Path(__file__).resolve().parents[1]))

import docker
from lab.voting_app import SCENARIOS,VotingAppLab


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--manifest',type=Path,default=Path('lab/voting-app-manifest.json'))
    p.add_argument('--helper-image',required=True)
    p.add_argument('--scenario',choices=[*SCENARIOS,'all'],default='healthy')
    p.add_argument('--allow-fault-injection',action='store_true')
    p.add_argument('--restore-only',action='store_true',help='Restore only the manifest-owned lab and verify a full vote')
    p.add_argument('--output',type=Path,default=Path('experiments/data/phase2c'))
    args=p.parse_args(argv)
    if (args.scenario!='healthy' or args.restore_only) and not args.allow_fault_injection:
        p.error('Fault injection/restoration requires --allow-fault-injection')
    client=docker.from_env(timeout=10)
    try:
        lab=VotingAppLab(client,json.loads(args.manifest.read_text()),args.helper_image,args.output)
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
