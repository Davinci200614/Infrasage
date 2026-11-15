"""Bounded helper execution using an existing DockerCollector and Docker client."""
from datetime import datetime, timezone
import hashlib
import ipaddress
import json
import math
from pathlib import Path
import re
import time
import uuid

from docker.errors import NotFound
from docker.types import LogConfig

from backend.collectors.tcp_probe_helper import PROTOCOL

CONFIG_KEYS = ('redis_host', 'redis_port', 'connection_timeout', 'execution_deadline', 'helper_image')
CLEANUP_RESERVE_SECONDS = 2.0


def helper_source_sha():
    return hashlib.sha256(Path(__file__).with_name('tcp_probe_helper.py').read_bytes()).hexdigest()


def valid_probe_config(config):
    if not isinstance(config, dict):
        return False
    host = config.get('redis_host')
    if not isinstance(host, str) or not host or len(host) > 253:
        return False
    try:
        ipaddress.ip_address(host)
    except ValueError:
        if not all(re.fullmatch(r'[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?', label)
                   for label in host.split('.')):
            return False
    port, timeout, deadline = (config.get(k) for k in ('redis_port', 'connection_timeout', 'execution_deadline'))
    if type(port) is not int or not 1 <= port <= 65535:
        return False
    if any(isinstance(v, bool) or not isinstance(v, (int, float)) for v in (timeout, deadline)):
        return False
    if not .1 <= timeout <= 3 or not 3 <= deadline <= 10 or deadline < timeout + CLEANUP_RESERVE_SECONDS:
        return False
    return (math.isfinite(timeout) and math.isfinite(deadline)
            and isinstance(config.get('helper_image'), str)
            and re.fullmatch(r'sha256:[0-9a-f]{64}', config['helper_image']) is not None)


def run_worker_probe(collector, target, config, *, deadline_at=None):
    """No image pulls/builds. Deadline includes a reserved cleanup interval.

    The Docker client must not be used concurrently during this operation: its
    request timeout is temporarily limited to the remaining overall budget.
    """
    start = time.monotonic()
    if not valid_probe_config(config):
        return {'source': 'docker', 'status': 'unavailable', 'data': None,
                'error': 'Invalid endpoint, timeout, deadline or pinned helper image',
                'probe_config': dict(config), 'observed_at': None,
                'retrieved_at': datetime.now(timezone.utc).isoformat(),
                'duration_seconds': time.monotonic() - start}
    overall_end = start + config['execution_deadline'] if deadline_at is None else deadline_at
    active_end = overall_end - CLEANUP_RESERVE_SECONDS
    client = collector.client
    original_timeout = client.api.timeout
    helper = None
    helper_name = 'infrasage-tcp-' + uuid.uuid4().hex
    result = {'source': 'docker', 'status': 'failed', 'data': None, 'observed_at': None,
              'probe_config': dict(config), 'docker_endpoint': client.api.base_url,
              'helper_name': helper_name, 'measured_energy_joules': None}

    def call(function, *args, cleanup=False, **kwargs):
        remaining = (overall_end if cleanup else active_end) - time.monotonic()
        if remaining <= 0:
            raise TimeoutError('Overall probe execution deadline exceeded')
        client.api.timeout = remaining
        return function(*args, **kwargs)

    try:
        if not valid_probe_config(config):
            result.update(status='unavailable', error='Invalid endpoint, timeout, deadline or pinned helper image')
            return result
        inspection = call(collector.inspect_runtime, **target)
        result.update({key: inspection[key] for key in ('target', 'target_identifier') if key in inspection})
        if inspection['status'] != 'completed':
            result.update(status=inspection['status'], error=inspection.get('error', 'Worker unavailable'))
            return result
        if inspection['data']['status'] != 'running':
            result.update(status='unavailable', error='Worker container is not running')
            return result
        worker_id = inspection['target']['container_id']
        image = call(client.images.get, config['helper_image'])
        labels = image.attrs.get('Config', {}).get('Labels') or {}
        if (image.id != config['helper_image'] or labels.get('org.infrasage.tcp-probe') != 'v1'
                or labels.get('org.infrasage.tcp-probe.sha256') != helper_source_sha()):
            result.update(status='unavailable', error='Local helper image is not the configured probe build')
            return result
        request_id = uuid.uuid4().hex
        result['request_id'] = request_id
        request = {**config, 'worker_id': worker_id, 'request_id': request_id,
                   'probe_deadline': max(.1, active_end - time.monotonic())}
        helper = call(client.containers.create, image.id, command=[json.dumps(request)],
                      entrypoint=['python3', '-I', '/opt/infrasage-tcp-probe.py'],
                      name=helper_name, network_mode='container:' + worker_id,
                      privileged=False, cap_drop=['ALL'], security_opt=['no-new-privileges:true'],
                      user='65534:65534', read_only=True, mem_limit='64m', pids_limit=32,
                      nano_cpus=250000000, healthcheck={'test': ['NONE']},
                      labels={'org.infrasage.tcp-probe.helper': request_id},
                      log_config=LogConfig(type='json-file', config={'max-size': '16k', 'max-file': '1'}))
        result['helper_id'] = helper.id
        if helper.id == worker_id:
            helper = None
            raise RuntimeError('Docker returned the worker as the helper identity')
        call(helper.reload)
        if (helper.attrs.get('HostConfig', {}).get('NetworkMode') != 'container:' + worker_id
                or helper.attrs.get('Image') != image.id):
            raise RuntimeError('Helper image or worker network namespace mismatch')
        call(helper.start)
        waited = call(helper.wait, timeout=max(.01, active_end - time.monotonic()))
        output = call(helper.logs, stdout=True, stderr=False, tail=1, stream=False)
        if not isinstance(output, bytes) or len(output) > 4096:
            raise RuntimeError('Missing or oversized probe result')
        payload = json.loads(output)
        if (not isinstance(payload, dict) or payload.get('protocol') != PROTOCOL
                or payload.get('request_id') != request_id or payload.get('worker_id') != worker_id
                or payload.get('endpoint') != {'host': config['redis_host'], 'port': config['redis_port']}):
            raise RuntimeError('Malformed or mismatched helper result')
        result['probe_result'] = payload
        if (waited.get('StatusCode') != 0 or payload.get('status') != 'completed'
                or payload.get('outcome') not in ('connected', 'refused', 'timeout') or payload.get('error')):
            raise RuntimeError(payload.get('error', 'Probe failed without a valid socket result'))
        stamp = datetime.fromisoformat(payload['observed_at'])
        now = datetime.now(timezone.utc)
        if stamp.tzinfo is None or stamp.utcoffset() is None or not start <= time.monotonic() <= active_end:
            raise RuntimeError('Invalid probe sample timestamp or execution deadline')
        # Compare wall-clock times independently of the monotonic execution budget.
        before = datetime.fromisoformat(inspection['observed_at'])
        if not before <= stamp <= now:
            raise RuntimeError('Missing, stale or future probe sample timestamp')
        # The worker must still be the same running target after the socket attempt.
        after = call(collector.inspect_runtime, container_id=worker_id)
        if (after['status'] != 'completed' or after['data']['status'] != 'running'
                or after.get('target') != inspection['target']):
            raise RuntimeError('Worker changed or became unavailable during probe')
        result.update(status='completed', observed_at=payload['observed_at'],
                      namespace='container:' + worker_id, helper_image=image.id,
                      data={'id': worker_id, 'name': inspection['target']['container_name'],
                            'outcome': payload['outcome'], 'endpoint': payload['endpoint']})
        return result
    except NotFound as error:
        result.update(status='failed' if 'request_id' in locals() else 'unavailable', error=f'Docker target or local helper image unavailable: {error}')
        return result
    except Exception as error:
        result.update(status='failed', error=f'Probe execution failed: {error}')
        return result
    finally:
        try:
            # If create timed out after reaching Docker, look up our unique name so
            # an orphaned create can still be cleaned up. Never touch the worker.
            if helper is None and result.get('helper_id') is None and 'request_id' in locals():
                try:
                    candidate = call(client.containers.get, helper_name, cleanup=True)
                    if (candidate.id == worker_id or candidate.attrs.get('Config', {}).get('Labels', {}).get(
                            'org.infrasage.tcp-probe.helper') != request_id):
                        raise RuntimeError('Refusing to remove an unrecognized helper identity')
                    helper = candidate
                except NotFound:
                    pass
            if helper is not None:
                call(helper.remove, force=True, v=True, cleanup=True)
                result['helper_removed'] = True
        except Exception as error:
            result['execution_error'] = result.get('error')
            result.update(status='failed', cleanup_error=str(error),
                          error='Probe helper cleanup failed; inspect helper_id/helper_name')
        finally:
            client.api.timeout = original_timeout
            result['retrieved_at'] = datetime.now(timezone.utc).isoformat()
            result['duration_seconds'] = time.monotonic() - start
