"""One real isolated server, two loopback families, no wildcard network listener."""
import os
import socket
import subprocess
import sys
import time

import httpx

from synwevo_server.run import loopback_sockets


def test_local_server_health_on_both_loopback_families(tmp_path):
    sockets = loopback_sockets(0)
    port = sockets[0].getsockname()[1]
    assert {s.getsockname()[0] for s in sockets} == ({'127.0.0.1','::1'} if socket.has_ipv6 else {'127.0.0.1'})
    assert len({s.getsockname()[1] for s in sockets}) == 1
    for s in sockets:
        s.close()
    env = {k:v for k,v in os.environ.items() if not k.startswith(('SYNWEVO_','DOCWEAVE_'))}
    env.update(SYNWEVO_DATA_DIR=str(tmp_path/'isolated'), SYNWEVO_HOST='127.0.0.1',
               SYNWEVO_PORT=str(port), SYNWEVO_LOCAL_DUAL_STACK='1', SYNWEVO_ROLE='accounts',
               SYNWEVO_TEST_INSTANCE='1', SYNWEVO_UI_AUTH='session', SYNWEVO_MAIL_MODE='disabled')
    with (tmp_path/'server.log').open('w') as log:
        proc = subprocess.Popen([sys.executable,'-m','synwevo_server.run'],env=env,stdout=log,stderr=log)
        try:
            with httpx.Client(trust_env=False,timeout=1) as c:
                deadline=time.monotonic()+20
                while True:
                    try:
                        response=c.get(f'http://127.0.0.1:{port}/healthz')
                        if response.status_code==200:
                            break
                    except httpx.HTTPError:
                        pass
                    assert proc.poll() is None and time.monotonic()<deadline, 'isolated dual-stack server failed to start'
                    time.sleep(.05)
                assert response.json()['status']=='ok'
                # Synthetic query markers stand for credentials and PII. The real access
                # logger must preserve the route/status without persisting any query data.
                assert c.get(f'http://127.0.0.1:{port}/healthz', params={
                    'code': 'synthetic-secret-access-log', 'email': 'synthetic-pii@example.test'
                }).status_code == 200
                if socket.has_ipv6:
                    assert c.get(f'http://[::1]:{port}/healthz').json()['status']=='ok'
        finally:
            proc.terminate()
            proc.wait(timeout=10)
    logged = (tmp_path/'server.log').read_text()
    assert 'GET /healthz HTTP/' in logged
    assert 'synthetic-secret-access-log' not in logged
    assert 'synthetic-pii' not in logged


def test_second_bind_failure_closes_first_socket():
    if not socket.has_ipv6:
        return
    with socket.socket(socket.AF_INET6,socket.SOCK_STREAM) as occupied:
        occupied.setsockopt(socket.IPPROTO_IPV6,socket.IPV6_V6ONLY,1)
        occupied.bind(('::1',0)); occupied.listen(1)
        port=occupied.getsockname()[1]
        try:
            loopback_sockets(port)
        except OSError:
            pass
        else:
            raise AssertionError('an occupied IPv6 port must abort startup')
        with socket.socket(socket.AF_INET,socket.SOCK_STREAM) as available:
            available.bind(('127.0.0.1',port))
