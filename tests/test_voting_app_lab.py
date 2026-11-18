import json
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from lab.voting_app import KIND,SCENARIOS,IsolationError,VotingAppLab,evaluate_investigation

TOKEN='a'*32
PROJECT='infrasage-voting-phase2c-'+TOKEN[:10]


@pytest.fixture
def manifest():
    names=['redis','db','worker','vote','result']
    return {'schema_version':1,'lab_kind':KIND,'project':PROJECT,'token':TOKEN,
            'services':{n:str(i+1)*64 for i,n in enumerate(names)},
            'networks':{'redis-tier':'6'*64,'db-tier':'7'*64},'volumes':[],
            'images':{n:{'id':'sha256:'+str(i+1)*64} for i,n in enumerate(names)},'source':'official sample'}


@pytest.fixture
def infrastructure(manifest):
    containers={};networks={};port=[6379]
    for service,cid in manifest['services'].items():
        c=Mock();c.id=cid;c.name=PROJECT+'-'+service+'-1'
        labels={'org.infrasage.lab':KIND,'org.infrasage.lab.token':TOKEN,'com.docker.compose.project':PROJECT,'com.docker.compose.service':service}
        c.attrs={'Image':manifest['images'][service]['id'],'State':{'Status':'running'},'Config':{'Labels':labels},
                 'NetworkSettings':{'Networks':{},'Ports':{'80/tcp':[{'HostIp':'127.0.0.1','HostPort':'38080'}]}}}
        c.logs.return_value=b'Connected to db\nFound redis at 172.20.0.3\n'
        def stop(timeout=5,c=c):c.attrs['State']['Status']='exited'
        def start(c=c):c.attrs['State']['Status']='running'
        c.stop.side_effect=stop;c.start.side_effect=start
        c.restart.side_effect=lambda timeout=5,c=c:c.attrs['State'].update(Status='running')
        containers[cid]=c
    for role,nid in manifest['networks'].items():
        n=Mock();n.id=nid;n.name=PROJECT+'_'+role
        n.attrs={'Labels':{'org.infrasage.lab':KIND,'org.infrasage.lab.token':TOKEN,
                          'com.docker.compose.project':PROJECT,'com.docker.compose.network':role}}
        def disconnect(worker,n=n):worker.attrs['NetworkSettings']['Networks'].pop(n.name)
        def connect(worker,aliases,n=n):worker.attrs['NetworkSettings']['Networks'][n.name]={'Aliases':aliases,'IPAddress':'172.20.0.4'}
        n.disconnect.side_effect=disconnect;n.connect.side_effect=connect;networks[nid]=n
    for service,cid in manifest['services'].items():
        for role in (('redis-tier','db-tier') if service=='worker' else ('redis-tier',) if service in ('redis','vote') else ('db-tier',)):
            n=networks[manifest['networks'][role]]
            containers[cid].attrs['NetworkSettings']['Networks'][n.name]={'Aliases':[service],'IPAddress':'172.20.0.'+('3' if service=='redis' else '4')}
    def redis_exec(command):
        if command[-1]=='PING':output='PONG'
        elif command[-3:]==['SET','port','0']:port[0]=0;output='OK'
        elif command[-3:]==['SET','port','6379']:port[0]=6379;output='OK'
        elif command[-3:]==['CONFIG','GET','port']:output='port\n'+str(port[0])
        else:raise AssertionError(command)
        return SimpleNamespace(exit_code=0,output=output.encode())
    containers[manifest['services']['redis']].exec_run.side_effect=redis_exec
    containers[manifest['services']['db']].exec_run.return_value=SimpleNamespace(exit_code=0,output=b'a\n')
    client=Mock();client.containers.get.side_effect=lambda cid:containers[cid];client.networks.get.side_effect=lambda nid:networks[nid]
    return client,containers,networks,port


def investigation():
    return {'model':{'status':'synthetic_provisional_not_calibrated'},'investigation':{
        'result':{'decision':'ABSTAIN','diagnosed_hypothesis':None,'selections':[{'test':{'identifier':'redis_runtime_state'}},{'test':{'identifier':'worker_redis_connectivity'}}],
                  'observations':[{'test_identifier':tid,'execution_status':'completed','observed_outcome':o} for tid,o in [('redis_runtime_state','running'),('worker_redis_connectivity','connected')]],
                  'final_beliefs':{'other_unknown':1},'total_estimated_energy_joules':.55,'total_estimated_latency_seconds':.37},
        'elapsed_seconds':1.2,'evidence_records':[{},{}],'trace':[{'event':'selected'},{'event':'stopped'}]}}


@pytest.fixture
def lab(manifest,infrastructure,tmp_path):
    client,_,_,_=infrastructure
    obj=VotingAppLab(client,manifest,'sha256:'+'c'*64,tmp_path,investigator=Mock(return_value=investigation()))
    obj.verify_baseline=Mock(return_value={'verified':True})
    return obj


@pytest.mark.parametrize('scenario',[s for s in SCENARIOS if s!='healthy'])
def test_faults_require_opt_in_before_any_docker_access(lab,infrastructure,scenario):
    with pytest.raises(PermissionError):lab.run_scenario(scenario)
    infrastructure[0].containers.get.assert_not_called()


@pytest.mark.parametrize('mutation',[
    lambda m:m.update(project='production'),lambda m:m.update(token='bad'),
    lambda m:m['services'].update(redis='short'),lambda m:m['services'].update(worker=m['services']['redis']),
    lambda m:m['networks'].pop('redis-tier'),lambda m:m['services'].pop('result'),
])
def test_invalid_manifest_rejected(manifest,mutation,tmp_path):
    mutation(manifest)
    with pytest.raises(IsolationError):VotingAppLab(Mock(),manifest,'image',tmp_path)


@pytest.mark.parametrize('label',['org.infrasage.lab','org.infrasage.lab.token','com.docker.compose.project','com.docker.compose.service'])
def test_unowned_container_never_mutated(lab,infrastructure,manifest,label):
    redis=infrastructure[1][manifest['services']['redis']]
    redis.attrs['Config']['Labels'][label]='unrelated'
    with pytest.raises(IsolationError):lab.run_scenario('redis_stopped',allow_fault_injection=True)
    redis.stop.assert_not_called();redis.exec_run.assert_not_called()
    lab.investigator.assert_not_called()


def test_unowned_network_never_mutated(lab,infrastructure,manifest):
    network=infrastructure[2][manifest['networks']['redis-tier']]
    network.attrs['Labels']['org.infrasage.lab.token']='other'
    with pytest.raises(IsolationError):lab.run_scenario('worker_network_interruption',allow_fault_injection=True)
    network.disconnect.assert_not_called()


@pytest.mark.parametrize('scenario',list(SCENARIOS))
def test_fault_trace_capture_and_healthy_restoration(lab,infrastructure,manifest,scenario):
    before=lab.snapshot()
    result=lab.run_scenario(scenario,allow_fault_injection=True)
    assert result['fault_injection_verified'] and result['restoration']['success']
    assert result['successful_experiment']
    assert result['evaluation']['comparison']=='abstention'
    assert result['evaluation']['measured_energy_joules'] is None
    assert result['investigation']['trace']==investigation()['investigation']['trace']
    artifact=json.loads(open(result['artifact_path']).read())
    assert artifact['scenario']==scenario and artifact['ground_truth']==SCENARIOS[scenario]
    assert result['state_before']['services']['redis']['state']['Status']=='running'
    assert infrastructure[3][0]==6379
    assert infrastructure[1][manifest['services']['redis']].attrs['State']['Status']=='running'
    net=infrastructure[2][manifest['networks']['redis-tier']]
    assert net.name in infrastructure[1][manifest['services']['worker']].attrs['NetworkSettings']['Networks']
    if scenario!='healthy':infrastructure[1][manifest['services']['worker']].restart.assert_called_once()
    assert before['services']['redis']['state']['Status']=='running'  # frozen snapshot


def test_restoration_after_investigation_failure(lab,infrastructure,manifest):
    lab.investigator.side_effect=RuntimeError('collector unavailable')
    r=lab.run_scenario('redis_stopped',allow_fault_injection=True)
    assert r['experiment_error']=='collector unavailable'
    assert r['restoration']['success']
    assert not r['successful_experiment']
    infrastructure[1][manifest['services']['redis']].start.assert_called_once()


def test_cleanup_failure_saved_explicitly(lab):
    lab.restore=Mock(side_effect=RuntimeError('network restore failed'))
    r=lab.run_scenario('healthy')
    assert not r['restoration']['success'] and not r['successful_experiment']
    assert 'network restore failed' in r['restoration']['error']
    assert json.loads(open(r['artifact_path']).read())['restoration']['success'] is False


def test_partial_injection_failure_still_restores(lab,infrastructure,manifest):
    def partial(scenario):
        lab.owned_container('redis').stop(timeout=5)
        raise RuntimeError('fault verification interrupted')
    lab.inject=partial
    r=lab.run_scenario('redis_stopped',allow_fault_injection=True)
    assert not r['fault_injection_verified'] and r['restoration']['success']
    infrastructure[1][manifest['services']['redis']].start.assert_called_once()


def test_invalid_scenario_no_side_effects(lab,infrastructure):
    with pytest.raises(ValueError):lab.run_scenario('arbitrary command',allow_fault_injection=True)
    infrastructure[0].containers.get.assert_not_called()


def test_loopback_url_derived_from_actual_binding(lab,infrastructure,manifest):
    vote=infrastructure[1][manifest['services']['vote']]
    assert lab.service_url('vote')=='http://127.0.0.1:38080/'
    vote.attrs['NetworkSettings']['Ports']['80/tcp'][0]['HostIp']='0.0.0.0'
    with pytest.raises(IsolationError):lab.service_url('vote')


def test_baseline_checks_actual_vote_worker_database_path(manifest,infrastructure,tmp_path):
    session=Mock()
    for method in (session.get,session.post):method.return_value=SimpleNamespace(status_code=200,raise_for_status=Mock())
    lab=VotingAppLab(infrastructure[0],manifest,'helper',tmp_path,session=session)
    r=lab.verify_baseline(timeout_seconds=1)
    assert r['database_vote']=='a' and r['voter_id'].startswith('phase2c-')
    session.post.assert_called_once()
    command=infrastructure[1][manifest['services']['db']].exec_run.call_args.args[0]
    assert command[:5]==['psql','-U','postgres','-d','postgres']
    assert r['voter_id'] in command[-1]


@pytest.mark.parametrize('diagnosed,truth,comparison',[
    ('redis_process_stopped',SCENARIOS['redis_stopped'],'correct_known_diagnosis'),
    ('host_cpu_saturation',SCENARIOS['redis_stopped'],'incorrect_known_diagnosis'),
    ('redis_process_stopped',SCENARIOS['healthy'],'false_positive_on_healthy'),
])
def test_ground_truth_comparison_separates_wrong_diagnosis_and_healthy_false_positive(diagnosed,truth,comparison):
    value=investigation()['investigation'];value['result'].update(decision='DIAGNOSED',diagnosed_hypothesis=diagnosed)
    assert evaluate_investigation(value,truth)['comparison']==comparison


def test_pinned_official_image_lock_rejects_replacement(tmp_path):
    from scripts.setup_voting_app_lab import image_references,IMAGES
    path=tmp_path/'lock.json'
    lock={'schema_version':1,'images':{s:ref.split(':')[0]+'@sha256:'+'a'*64 for s,ref in IMAGES.items()}}
    path.write_text(json.dumps(lock))
    assert image_references(path)==lock['images']
    lock['images']['worker']='unrelated/image@sha256:'+'a'*64
    path.write_text(json.dumps(lock))
    with pytest.raises(ValueError):image_references(path)


def test_optional_ui_network_requires_matching_ownership(lab,manifest,infrastructure):
    uid='8'*64
    manifest['networks']['ui-tier']=uid
    network=Mock();network.id=uid;network.attrs={'Labels':{'org.infrasage.lab':KIND,'org.infrasage.lab.token':'wrong',
        'com.docker.compose.project':PROJECT,'com.docker.compose.network':'ui-tier'}}
    infrastructure[2][uid]=network
    with pytest.raises(IsolationError):lab.validate_resources()
