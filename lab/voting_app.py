"""Opt-in experiments on an explicitly owned official Voting App lab, not a twin."""
from copy import deepcopy
from dataclasses import asdict
from datetime import datetime, timezone
import ipaddress
import json
from pathlib import Path
import re
import time
import uuid

import requests

from backend.core.collector_manager import CollectorManager
from backend.core.orchestrator import LiveInvestigationConfig, StoppingConfig, run_live_investigation
from backend.diagnosis.catalogue_loader import load_catalogues
from backend.optimization.planner import DiagnosticPolicy

KIND='voting-app-phase2c'
SERVICES={'redis','db','worker','vote','result'}
SCENARIOS={
    'healthy':{'fault':'none','catalogue_hypothesis':None},
    'redis_stopped':{'fault':'lab Redis container stopped','catalogue_hypothesis':'redis_process_stopped'},
    'redis_port_unavailable':{'fault':'Redis TCP port set to 0; Unix socket and process remain available',
                              'catalogue_hypothesis':'redis_service_degraded',
                              'mapping_note':'Provisional category mapping: running Redis with its TCP listener disabled'},
    'worker_network_interruption':{'fault':'Only worker detached from owned redis-tier network; database tier retained',
                                   'catalogue_hypothesis':'worker_redis_network_failure'},
}


class IsolationError(ValueError):
    pass


def validate_manifest(manifest):
    if not isinstance(manifest,dict) or manifest.get('schema_version')!=1 or manifest.get('lab_kind')!=KIND:
        raise IsolationError('Unsupported lab manifest')
    token=manifest.get('token','')
    if not isinstance(token,str) or not re.fullmatch('[0-9a-f]{32}',token):
        raise IsolationError('Invalid lab ownership token')
    if manifest.get('project')!='infrasage-voting-phase2c-'+token[:10]:
        raise IsolationError('Project does not match the isolated manifest token')
    if set(manifest.get('services',{}))!=SERVICES:
        raise IsolationError('Manifest must identify all five actual sample services')
    if not {'redis-tier','db-tier'} <= set(manifest.get('networks',{})) <= {'redis-tier','db-tier','ui-tier'}:
        raise IsolationError('Manifest must identify both isolated networks')
    ids=list(manifest['services'].values())+list(manifest['networks'].values())
    if any(not isinstance(v,str) or not re.fullmatch('[0-9a-f]{64}',v) for v in ids) or len(set(ids))!=len(ids):
        raise IsolationError('Missing, ambiguous or duplicate resource identities')
    if set(manifest.get('images',{}))!=SERVICES:
        raise IsolationError('Pinned application image identities required')
    return manifest


def evaluate_investigation(investigation,ground_truth):
    result=investigation['result']
    decision=result['decision']
    diagnosed=result['diagnosed_hypothesis']
    if decision=='ABSTAIN':comparison='abstention'
    elif decision=='DIAGNOSED' and ground_truth['catalogue_hypothesis'] is None:comparison='false_positive_on_healthy'
    elif decision=='DIAGNOSED':
        comparison='correct_known_diagnosis' if diagnosed==ground_truth['catalogue_hypothesis'] else 'incorrect_known_diagnosis'
    else:comparison='invalid_nonterminal_result'
    observations=result['observations']
    return {'comparison':comparison,'calibrated_accuracy_claim':False,
            'selected_test_order':[s['test']['identifier'] for s in result['selections']],
            'outcomes':[{'test_id':o['test_identifier'],'status':o['execution_status'],'outcome':o['observed_outcome']} for o in observations],
            'all_attempted_observations_completed':bool(observations) and all(o['execution_status']=='completed' for o in observations),
            'both_supported_tests_completed':len(observations)==2 and all(o['execution_status']=='completed' for o in observations),
            'admitted_evidence_count':len(investigation['evidence_records']),
            'posterior_sum':sum(result['final_beliefs'].values()),
            'measured_energy_joules':None,
            'estimated_energy_joules':result['total_estimated_energy_joules'],
            'estimated_latency_seconds':result['total_estimated_latency_seconds'],
            'actual_investigation_seconds':investigation['elapsed_seconds']}


class VotingAppLab:
    def __init__(self,client,manifest,helper_image,output_directory,*,session=None,investigator=None):
        self.client=client
        self.manifest=validate_manifest(manifest)
        self.helper_image=helper_image
        self.output_directory=Path(output_directory)
        self.session=session or requests.Session()
        self.session.trust_env=False
        self.investigator=investigator or self._investigate

    def _labels(self,labels,*,service=None,network=None):
        expected={'org.infrasage.lab':KIND,'org.infrasage.lab.token':self.manifest['token'],
                  'com.docker.compose.project':self.manifest['project']}
        if service is not None:expected['com.docker.compose.service']=service
        if network is not None:expected['com.docker.compose.network']=network
        if not isinstance(labels,dict) or any(labels.get(k)!=v for k,v in expected.items()):
            raise IsolationError('Resource ownership labels do not match this lab')

    def owned_container(self,service):
        if service not in SERVICES:raise IsolationError('Unsupported service target')
        container=self.client.containers.get(self.manifest['services'][service])
        container.reload()
        self._labels(container.attrs.get('Config',{}).get('Labels'),service=service)
        if container.id!=self.manifest['services'][service] or container.attrs.get('Image')!=self.manifest['images'][service]['id']:
            raise IsolationError('Container or pinned sample image identity changed')
        return container

    def owned_network(self,role):
        if role not in self.manifest['networks']:raise IsolationError('Unsupported network target')
        network=self.client.networks.get(self.manifest['networks'][role])
        network.reload()
        self._labels(network.attrs.get('Labels'),network=role)
        if network.id!=self.manifest['networks'][role]:raise IsolationError('Network identity mismatch')
        return network

    def validate_resources(self):
        for service in SERVICES:self.owned_container(service)
        for role in self.manifest['networks']:self.owned_network(role)

    def snapshot(self):
        self.validate_resources()
        services={}
        for service in sorted(SERVICES):
            c=self.owned_container(service)
            services[service]={'id':c.id,'name':c.name,'image':c.attrs['Image'],
                               'state':deepcopy(c.attrs.get('State')),
                               'networks':deepcopy(c.attrs.get('NetworkSettings',{}).get('Networks',{})),
                               'ports':deepcopy(c.attrs.get('NetworkSettings',{}).get('Ports',{}))}
        worker=self.owned_container('worker')
        return {'captured_at':datetime.now(timezone.utc).isoformat(),'services':services,
                'worker_logs':worker.logs(tail=30,timestamps=True).decode('utf-8',errors='replace')}

    def service_url(self,service):
        if service not in ('vote','result'):raise IsolationError('Only lab UI HTTP endpoints are supported')
        bindings=self.owned_container(service).attrs.get('NetworkSettings',{}).get('Ports',{}).get('80/tcp')
        if not isinstance(bindings,list) or len(bindings)!=1 or bindings[0].get('HostIp')!='127.0.0.1':
            raise IsolationError('UI endpoint is not a single loopback-only binding')
        port=bindings[0].get('HostPort','')
        if not str(port).isdigit() or not 1<=int(port)<=65535:raise IsolationError('Malformed UI port')
        return 'http://127.0.0.1:'+str(port)+'/'

    def redis_command(self,*arguments):
        result=self.owned_container('redis').exec_run(['redis-cli','-s','/tmp/redis.sock',*arguments])
        output=result.output.decode('utf-8',errors='replace').strip()
        if result.exit_code!=0 or output.startswith('ERR'):
            raise RuntimeError('Owned Redis command failed: '+output)
        return output

    def verify_baseline(self,timeout_seconds=75):
        self.validate_resources()
        end=time.monotonic()+timeout_seconds
        last_error='not attempted'
        while time.monotonic()<end:
            try:
                if any(self.owned_container(s).attrs.get('State',{}).get('Status')!='running' for s in SERVICES):
                    raise RuntimeError('One of the five sample services is not running')
                if self.redis_command('PING')!='PONG' or self.redis_command('CONFIG','GET','port').splitlines()!=['port','6379']:
                    raise RuntimeError('Redis baseline listener is not restored')
                vote_url,result_url=self.service_url('vote'),self.service_url('result')
                vote_get=self.session.get(vote_url,timeout=5);vote_get.raise_for_status()
                result_get=self.session.get(result_url,timeout=5);result_get.raise_for_status()
                # A unique controlled lab vote must traverse Flask -> Redis -> .NET
                # worker -> PostgreSQL. This is stronger than container health alone.
                voter='phase2c-'+uuid.uuid4().hex
                self.session.cookies.clear()
                response=self.session.post(vote_url,data={'vote':'a'},cookies={'voter_id':voter},timeout=5)
                response.raise_for_status()
                sql="SELECT vote FROM votes WHERE id = '"+voter+"';"
                while time.monotonic()<end:
                    db=self.owned_container('db')
                    query=db.exec_run(['psql','-U','postgres','-d','postgres','-Atc',sql])
                    if query.exit_code==0 and query.output.decode().strip()=='a':
                        return {'verified_at':datetime.now(timezone.utc).isoformat(),
                                'vote_url':vote_url,'result_url':result_url,'voter_id':voter,
                                'vote_http_status':response.status_code,'result_http_status':result_get.status_code,
                                'database_vote':'a','path':'actual vote HTTP -> Redis -> sample .NET worker -> PostgreSQL',
                                'result_scores_stream_verified':False}
                    if self.owned_container('worker').attrs.get('State',{}).get('Status')!='running':
                        raise RuntimeError('Sample worker exited before vote reached PostgreSQL')
                    time.sleep(.25)
                raise TimeoutError('Vote did not reach PostgreSQL')
            except (requests.RequestException,RuntimeError) as error:
                last_error=str(error)
                time.sleep(.25)
        raise TimeoutError('Healthy baseline not verified: '+last_error)

    def inject(self,scenario):
        self.validate_resources()
        if scenario=='healthy':return {'method':'none'}
        if scenario=='redis_stopped':
            redis=self.owned_container('redis');redis.stop(timeout=5);redis.reload()
            if redis.attrs.get('State',{}).get('Status')!='exited':raise RuntimeError('Redis stop did not take effect')
            return {'method':'Docker stop','container_id':redis.id,'verified_state':'exited'}
        if scenario=='redis_port_unavailable':
            self.redis_command('CONFIG','SET','port','0')
            if self.redis_command('CONFIG','GET','port').splitlines()!=['port','0']:
                raise RuntimeError('Redis TCP listener disable was not verified')
            if self.owned_container('redis').attrs['State']['Status']!='running':raise RuntimeError('Redis process unexpectedly stopped')
            return {'method':'Redis CONFIG SET port 0 over owned Unix socket','process_running':True,'tcp_port':0}
        if scenario=='worker_network_interruption':
            network=self.owned_network('redis-tier');worker=self.owned_container('worker')
            endpoint=worker.attrs.get('NetworkSettings',{}).get('Networks',{}).get(network.name)
            if not endpoint:raise IsolationError('Worker is not attached to the owned Redis tier')
            network.disconnect(worker)
            worker=self.owned_container('worker')
            if network.name in worker.attrs.get('NetworkSettings',{}).get('Networks',{}):raise RuntimeError('Network disconnect not applied')
            return {'method':'Docker network disconnect','worker_id':worker.id,'network_id':network.id,
                    'original_aliases':endpoint.get('Aliases',[]),'db_tier_not_disconnected':True}
        raise ValueError('Unknown scenario')

    def restore(self,before,*,restart_worker):
        self.validate_resources()
        redis=self.owned_container('redis')
        if redis.attrs.get('State',{}).get('Status')!='running':redis.start()
        end=time.monotonic()+20
        while True:
            try:
                if self.redis_command('PING')=='PONG':break
            except Exception:
                if time.monotonic()>end:raise
            if time.monotonic()>end:raise TimeoutError('Redis Unix socket did not recover')
            time.sleep(.2)
        self.redis_command('CONFIG','SET','port','6379')
        network=self.owned_network('redis-tier');worker=self.owned_container('worker')
        if network.name not in worker.attrs.get('NetworkSettings',{}).get('Networks',{}):
            aliases=before['services']['worker']['networks'].get(network.name,{}).get('Aliases') or [worker.name,'worker']
            network.connect(worker,aliases=aliases)
        if restart_worker:
            # The official worker can exit on Redis failures. Restart only this
            # owned worker after restoring Redis/network so it resolves the live IP.
            self.owned_container('worker').restart(timeout=5)
        healthy=self.verify_baseline()
        return {'success':True,'healthy':healthy,'state':self.snapshot()}

    def _investigate(self,redis_id,worker_id,redis_ip):
        loaded=load_catalogues(research_mode=True)
        manager=CollectorManager(loaded)
        try:
            config=LiveInvestigationConfig(redis_target={'container_id':redis_id},worker_target={'container_id':worker_id},
                probe_config={'redis_host':redis_ip,'redis_port':6379,'helper_image':self.helper_image,
                              'connection_timeout':.5,'execution_deadline':8.0},maximum_duration_seconds=30)
            result=run_live_investigation(loaded,manager,config,DiagnosticPolicy('ratio'),
                                           StoppingConfig(max_tests=2,max_energy_joules=1.0))
            return {'model':{k:loaded.metadata['model'][k] for k in ('model_id','version','status')},
                    'investigation':asdict(result)}
        finally:manager.close()

    def run_scenario(self,scenario,*,allow_fault_injection=False):
        if scenario not in SCENARIOS:raise ValueError('Unknown experiment scenario')
        if scenario!='healthy' and allow_fault_injection is not True:
            raise PermissionError('Fault scenarios require explicit --allow-fault-injection')
        self.validate_resources()
        baseline=self.verify_baseline()
        before=self.snapshot()
        redis_network=self.owned_network('redis-tier')
        redis_ip=before['services']['redis']['networks'][redis_network.name]['IPAddress']
        ipaddress.ip_address(redis_ip)
        run_id=uuid.uuid4().hex
        record={'schema_version':1,'run_id':run_id,'scenario':scenario,'project':self.manifest['project'],
                'ground_truth':dict(SCENARIOS[scenario]),'images':self.manifest['images'],
                'source':self.manifest.get('source'),'started_at':datetime.now(timezone.utc).isoformat(),
                'baseline_before':baseline,'state_before':before,
                'diagnostic_endpoint':{'host':redis_ip,'port':6379,
                    'selection':'actual Redis IP discovered before injection on the owned Redis tier; retained during fault'},
                'fault_injection_verified':False,'restoration':None}
        try:
            record['injection']=self.inject(scenario)
            record['fault_injection_verified']=True
            record['state_during_fault']=self.snapshot()
            outcome=self.investigator(self.manifest['services']['redis'],self.manifest['services']['worker'],redis_ip)
            record.update(outcome)
            record['evaluation']=evaluate_investigation(record['investigation'],record['ground_truth'])
        except Exception as error:
            record['experiment_error']=str(error)
        finally:
            try:record['restoration']=self.restore(before,restart_worker=scenario!='healthy')
            except Exception as error:record['restoration']={'success':False,'error':str(error)}
            record['finished_at']=datetime.now(timezone.utc).isoformat()
            record['successful_experiment']=('experiment_error' not in record and record['restoration']['success'])
            self.output_directory.mkdir(parents=True,exist_ok=True)
            path=self.output_directory/(run_id+'-'+scenario+'.json')
            record['artifact_path']=str(path)
            path.write_text(json.dumps(record,indent=2,default=str,allow_nan=False)+'\n')
        return record
