"""Fixed helper program copied into the locally built InfraSage TCP probe image."""
import errno
import json
import signal
import socket
import sys
import time
from datetime import datetime, timezone

PROTOCOL = 'infrasage-tcp-v1'


class ProbeDeadline(RuntimeError):
    pass


def run_probe(config):
    """No shell, no Redis commands, no payload: resolve and attempt one TCP endpoint."""
    result = {'protocol': PROTOCOL, 'request_id': config['request_id'],
              'worker_id': config['worker_id'],
              'endpoint': {'host': config['redis_host'], 'port': config['redis_port']}}
    try:
        addresses = socket.getaddrinfo(config['redis_host'], config['redis_port'],
                                       type=socket.SOCK_STREAM, proto=socket.IPPROTO_TCP)
        if not addresses or len(addresses) > 16:
            raise RuntimeError('Empty or oversized DNS answer')
        deadline = time.monotonic() + config['connection_timeout']
        outcome = 'refused'
        for family, socktype, proto, _, address in addresses:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                outcome = 'timeout'
                break
            try:
                with socket.socket(family, socktype, proto) as sock:
                    sock.settimeout(remaining)
                    sock.connect(address)
                outcome = 'connected'
                result['resolved_address'] = list(address)
                break
            except (socket.timeout, TimeoutError):
                outcome = 'timeout'
                break
            except OSError as error:
                if error.errno == errno.ECONNREFUSED:
                    continue
                if error.errno == errno.ETIMEDOUT:
                    outcome = 'timeout'
                    break
                raise
        result.update(status='completed', outcome=outcome)
    except socket.gaierror as error:
        result.update(status='failed', error=f'DNS resolution failed: {error}')
    except ProbeDeadline:
        result.update(status='failed', error='Helper execution deadline exceeded without a reliable socket result')
    except Exception as error:
        result.update(status='failed', error=f'Internal probe error: {error}')
    result['observed_at'] = datetime.now(timezone.utc).isoformat()
    return result


def main():
    config = json.loads(sys.argv[1])
    def expired(*_):
        raise ProbeDeadline()
    signal.signal(signal.SIGALRM, expired)
    signal.setitimer(signal.ITIMER_REAL, config['probe_deadline'])
    try:
        result = run_probe(config)
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
    print(json.dumps(result, allow_nan=False), flush=True)
    return 0 if result['status'] == 'completed' else 2


if __name__ == '__main__':
    raise SystemExit(main())
