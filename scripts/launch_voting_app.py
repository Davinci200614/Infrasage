#!/usr/bin/env python3
"""Resolve ownership-checked lab targets for the friendly shell launcher."""
import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if __package__ in (None, ''):
    sys.path.insert(0, str(ROOT))

import docker
from backend.collectors.tcp_probe import helper_source_sha
from lab.voting_app import IsolationError, VotingAppLab
from scripts.demo_live_investigation import main as investigate


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('start', 'status', 'investigate'))
    args = parser.parse_args(argv)
    manifest_path = ROOT / 'lab/voting-app-manifest.json'
    if not manifest_path.exists():
        parser.exit(1, 'No Voting App manifest. Run ./infrasage.sh start first.\n')
    client = None
    try:
        client = docker.from_env(timeout=10)
        lab = VotingAppLab(client, json.loads(manifest_path.read_text()), '',
                           ROOT / 'experiments/data/phase2c')
        lab.validate_resources()
        if args.action == 'status':
            for service in sorted(lab.manifest['services']):
                container = lab.owned_container(service)
                print(f'{service}: {container.status}')
            for service in ('vote', 'result'):
                if lab.owned_container(service).status != 'running':
                    print(f'{service} URL: unavailable while stopped; run ./infrasage.sh start')
                    continue
                try:
                    print(f'{service} URL: {lab.service_url(service)}')
                except IsolationError as error:
                    print(f'{service} URL unavailable: {error}')
            return 0
        try:
            helper = client.images.get('infrasage-tcp-probe:phase2b1')
        except docker.errors.ImageNotFound:
            raise RuntimeError('TCP probe image missing. Run ./infrasage.sh setup.') from None
        labels = helper.attrs.get('Config', {}).get('Labels') or {}
        if (labels.get('org.infrasage.tcp-probe') != 'v1'
                or labels.get('org.infrasage.tcp-probe.sha256') != helper_source_sha()):
            raise RuntimeError('TCP probe image is outdated. Run ./infrasage.sh setup.')
        if args.action == 'start':
            print('Verifying a synthetic vote reaches PostgreSQL...', flush=True)
            lab.verify_baseline()
            print(f'Vote:   {lab.service_url("vote")}', flush=True)
            print(f'Result: {lab.service_url("result")}', flush=True)
        redis = lab.owned_container('redis')
        network = lab.owned_network('redis-tier')
        attachments = redis.attrs['NetworkSettings']['Networks']
        endpoints = [value for value in attachments.values() if value['NetworkID'] == network.id]
        if len(endpoints) > 1:
            raise RuntimeError('Ambiguous owned Redis-tier endpoint.')
        endpoint = endpoints[0].get('IPAddress') if endpoints else None
        probe_args = ['--redis-host', endpoint, '--redis-port', '6379',
                      '--helper-image', helper.id] if endpoint else ['--runtime-only']
        if not endpoint:
            print('Redis has no current IP on its owned network; checking runtime state only.', flush=True)
        print('Starting one research investigation; ABSTAIN means insufficient evidence.', flush=True)
        return investigate([
            '--redis-id', redis.id, '--worker-id', lab.owned_container('worker').id,
            *probe_args,
        ])
    except Exception as error:
        print(f'Launcher failed: {error}', file=sys.stderr)
        return 1
    finally:
        if client is not None:
            client.close()


if __name__ == '__main__':
    raise SystemExit(main())
