"""Synthetic images/invites; role checks and lifecycle through real identity routes."""
import base64
import struct
import zlib
from concurrent.futures import ThreadPoolExecutor

from fastapi.testclient import TestClient

from synwevo_core.errors import ServiceError
from synwevo_core.organizations import Organizations
from synwevo_core.sessions import CSRF_COOKIE, CSRF_HEADER
from synwevo_core.store import Store
from synwevo_identity.invites import Invites
from services.identity.tests.test_organizations import env, create, request, reauth, BASE, REASON, PW
from services.identity.tests.test_self_service import png


def test_org_avatar_uses_private_validated_png_and_scoped_mutation(env):
    created = create(env)
    oid = created['organization']['id']
    path = BASE+'/'+oid+'/avatar'
    data = b'Comment\x00synthetic secret metadata'
    meta = struct.pack('>I',len(data)) + b'tEXt' + data + struct.pack('>I',zlib.crc32(b'tEXt'+data)&0xffffffff)
    body = {'image_base64':base64.b64encode(png(meta=meta)).decode(), 'reason':REASON}
    assert request(env,'PUT',path,body).status_code == 403
    reauth(env)
    assert request(env,'PUT',path,body).status_code == 200
    image=env['client'].get(path)
    assert image.status_code == 200 and image.content == png()
    assert image.headers['cache-control'] == 'no-store'
    assert env['client'].get(BASE+'/'+oid).json()['organization']['has_avatar'] is True
    assert TestClient(env['api']).get(path).status_code == 401
    outsider=TestClient(env['api'])
    assert outsider.post('/api/auth/login',json={'email':env['users']['outsider']['email'],'password':PW}).status_code == 200
    assert outsider.get(path).status_code == 404
    for bad in [b'<svg xmlns="http://www.w3.org/2000/svg"/>', png(width=513)]:
        response=request(env,'PUT',path,{'image_base64':base64.b64encode(bad).decode(),'reason':REASON})
        assert response.status_code == 400
        assert env['client'].get(path).content == png()
    assert request(env,'DELETE',path,{'reason':REASON}).status_code==200
    assert env['client'].get(path).status_code==404


def test_decline_needs_right_account_csrf_and_stays_consumed(env):
    created=create(env)
    wid=created['workspace']['id']
    invites=Invites(env['store'],env['ident'])
    row,token=invites.create(workspace_id=wid,email=env['users']['bob']['email'],role='viewer',actor_id=env['users']['alice']['id'])
    path='/api/auth/invitations/decline'
    assert request(env,'POST',path,{'token':token}).status_code==404
    bob=TestClient(env['api'])
    bob.post('/api/auth/login',json={'email':env['users']['bob']['email'],'password':PW})
    assert bob.get(path,params={'token':token}).status_code==404
    assert invites.get(row['id'])['revoked_at'] is None  # a scanner GET cannot consume it
    assert bob.post(path,json={'token':token}).status_code==403
    headers={CSRF_HEADER:bob.cookies.get(CSRF_COOKIE)}
    assert bob.post(path,json={'token':token},headers=headers).json()=={'state':'declined'}
    assert bob.post(path,json={'token':token},headers=headers).json()=={'state':'declined'}
    assert bob.post('/api/auth/invitations/accept',json={'token':token},headers=headers).json()['error']=='invite_declined'
    assert env['ident'].membership(wid,env['users']['bob']['id']) is None
    assert invites.list_for_workspace(wid,env['users']['alice']['id'],state='declined')[0]['id']==row['id']
    assert env['store'].one("SELECT COUNT(*) AS n FROM audit_events WHERE action='invite.decline'")['n']==2


def test_org_creation_budget_is_atomic_across_connections_and_replay_is_free(env):
    uid=env['users']['alice']['id']
    root=env['root']
    first=env['orgs'].create(uid,'org0','first','rate-limit-key-0')
    def create_one(i):
        try:
            Organizations(Store(root)).create(uid,f'org{i}','space',f'rate-limit-key-{i}')
            return 'ok'
        except ServiceError as exc:
            return exc.code
    with ThreadPoolExecutor(max_workers=4) as pool:
        results=list(pool.map(create_one,range(1,14)))
    assert results.count('ok')==9 and results.count('creation_rate_limited')==4
    assert env['orgs'].create(uid,'org0','first','rate-limit-key-0')==first
    assert env['store'].one('SELECT COUNT(*) AS n FROM organizations')['n']==10
