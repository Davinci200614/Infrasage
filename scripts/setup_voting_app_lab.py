#!/usr/bin/env python3
"""Explicit creation of an isolated official Voting App Compose project."""
import argparse
import json
import os
import re
from pathlib import Path
import subprocess
import sys
import uuid

if __package__ in (None, ''):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import docker

ROOT = Path(__file__).resolve().parents[1]
IMAGES = {'redis':'redis:7-alpine', 'db':'postgres:15-alpine',
          'worker':'dockersamples/examplevotingapp_worker:latest',
          'vote':'dockersamples/examplevotingapp_vote:latest',
          'result':'dockersamples/examplevotingapp_result:latest'}


def image_references(lock_path=ROOT/'lab/voting-app-images.lock.json'):
    """Use the recorded immutable official image references when available."""
    if not lock_path.exists():
        return dict(IMAGES)
    lock=json.loads(lock_path.read_text())
    if lock.get('schema_version')!=1 or set(lock.get('images',{}))!=set(IMAGES):
        raise ValueError('Invalid Voting App image lock')
    for service,reference in lock['images'].items():
        repository=IMAGES[service].split(':')[0]
        if not isinstance(reference,str) or not re.fullmatch(re.escape(repository)+r'@sha256:[0-9a-f]{64}',reference):
            raise ValueError('Image lock must contain pinned official sample references')
    return lock['images']


def compose_environment(manifest):
    return {**os.environ, 'INFRASAGE_LAB_TOKEN':manifest['token'],
            **{service.upper()+'_IMAGE':image['id'] for service,image in manifest['images'].items()}}


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    action=parser.add_mutually_exclusive_group(required=True)
    action.add_argument('--create',action='store_true')
    action.add_argument('--refresh',action='store_true',help='Reconcile only an existing ownership-verified lab project')
    parser.add_argument('--manifest',type=Path,default=ROOT/'lab/voting-app-manifest.json')
    args=parser.parse_args(argv)
    if args.create and args.manifest.exists():
        raise SystemExit('Manifest already exists; inspect/restore that owned lab instead of overwriting it')
    token=uuid.uuid4().hex
    manifest={'schema_version':1,'project':'infrasage-voting-phase2c-'+token[:10], 'token':token,
              'lab_kind':'voting-app-phase2c','images':{},'services':{},'networks':{},'volumes':[],
              'source':'https://github.com/dockersamples/example-voting-app/blob/main/docker-stack.yml'}
    client=docker.from_env(timeout=180)
    args.manifest.parent.mkdir(parents=True,exist_ok=True)
    try:
        if args.refresh:
            from lab.voting_app import VotingAppLab
            manifest=json.loads(args.manifest.read_text())
            VotingAppLab(client,manifest,'',ROOT/'experiments/data/phase2c').validate_resources()
            token=manifest['token']
            manifest['services']={}
            manifest['networks']={}
            manifest['volumes']=[]
        for service,reference in image_references().items():
            try:image=client.images.get(reference)
            except docker.errors.ImageNotFound:
                print('Pulling '+reference,flush=True);image=client.images.pull(reference)
            manifest['images'][service]={'reference':reference,'id':image.id,
                                         'repo_digests':image.attrs.get('RepoDigests',[]),
                                         'architecture':image.attrs.get('Architecture')}
        args.manifest.write_text(json.dumps(manifest,indent=2)+'\n')
        command=['docker','compose','-p',manifest['project'],'-f',str(ROOT/'lab/compose.yaml')]
        subprocess.run(command+['up','-d'],env=compose_environment(manifest),check=True,timeout=180)
        for container in client.containers.list(all=True,filters={'label':'com.docker.compose.project='+manifest['project']}):
            labels=container.labels
            if labels.get('org.infrasage.lab.token')!=token:
                raise RuntimeError('Unexpected project ownership; no further setup mutation permitted')
            service=labels['com.docker.compose.service']
            if service in manifest['services']:raise RuntimeError('Unexpected duplicate service')
            manifest['services'][service]=container.id
        for network in client.networks.list(filters={'label':'com.docker.compose.project='+manifest['project']}):
            if network.attrs.get('Labels',{}).get('org.infrasage.lab.token')!=token:raise RuntimeError('Network ownership mismatch')
            manifest['networks'][network.attrs['Labels']['com.docker.compose.network']]=network.id
        for volume in client.volumes.list(filters={'label':'com.docker.compose.project='+manifest['project']}):
            if volume.attrs.get('Labels',{}).get('org.infrasage.lab.token')!=token:raise RuntimeError('Volume ownership mismatch')
            manifest['volumes'].append(volume.name)
        if set(manifest['services'])!=set(IMAGES):raise RuntimeError('Incomplete five-service deployment')
        manifest['deployment_status']='created_needs_functional_verification'
        args.manifest.write_text(json.dumps(manifest,indent=2)+'\n')
        print(json.dumps({'manifest':str(args.manifest),'project':manifest['project'],'services':manifest['services']},indent=2))
    except Exception as error:
        manifest.update(deployment_status='setup_failed',setup_error=str(error))
        args.manifest.write_text(json.dumps(manifest,indent=2)+'\n')
        raise
    finally:client.close()


if __name__=='__main__':main()
