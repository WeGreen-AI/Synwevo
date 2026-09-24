"""Capability URL regressions using the isolated real account HTTP fixture."""
from urllib.parse import parse_qs, urlsplit
import re
import pytest

from fastapi.testclient import TestClient

from services.identity.tests.test_self_service import PW, account, mail_token, send


@pytest.mark.parametrize('locale', ['zh-Hans', 'zh-Hant', 'en'])
def test_deletion_fragment_and_body_preview_keep_capability_out_of_request_url(account, locale):
    assert send(account, 'PATCH', '/profile', {'locale': locale}).status_code == 200
    assert send(account, 'POST', '/deletion', {'password': PW}).status_code == 200
    row = account['store'].one("SELECT body FROM mail_outbox WHERE kind='account_recovery'")
    link = urlsplit(re.search(r'http://testserver/\S+', row['body'])[0])
    token = parse_qs(link.fragment)['token'][0]
    assert parse_qs(link.query) == {'lang': [locale]} and link.path == '/account/recovery'
    assert token == mail_token(account, 'account_recovery')
    with TestClient(account['app']) as anonymous:
        preview = anonymous.post('/api/auth/deletion/recovery/preview', json={'token': token})
        assert preview.status_code == 200
        assert token not in str(preview.request.url) and token not in preview.text
        assert preview.headers['cache-control'] == 'no-store'
        assert set(preview.json()) == {'status', 'deletion_due_at'}
        legacy = anonymous.get('/api/auth/deletion/recovery/preview', params={'token': token})
        assert legacy.json() == preview.json()
        assert anonymous.post('/api/auth/deletion/recovery', json={'token': token}).status_code == 200
        assert anonymous.post('/api/auth/deletion/recovery/preview', json={'token': token}).status_code == 410


def test_email_change_fragment_preserves_action_without_query_secret(account):
    assert send(account, 'POST', '/email-change', {'email': 'replacement@example.test', 'password': PW}).status_code == 200
    row = account['store'].one("SELECT body FROM mail_outbox WHERE kind='email_change'")
    link = urlsplit(re.search(r'http://testserver/\S+', row['body'])[0])
    assert link.path == '/account' and parse_qs(link.query) == {'lang': ['zh-Hans']}
    assert parse_qs(link.fragment) == {'token': [mail_token(account, 'email_change')], 'action': ['email-change']}


def test_body_preview_rejects_malformed_capabilities_without_profile_metadata(account):
    with TestClient(account['app']) as anonymous:
        for body, status in [({}, 410), ({'token': 'missing'}, 410), ({'token': []}, 400),
                             ({'token': 'x' * 129}, 400), ({'token': 'x' * 32, 'user_id': 'another'}, 400)]:
            response = anonymous.post('/api/auth/deletion/recovery/preview', json=body)
            assert response.status_code == status, response.text
            assert account['user']['email'] not in response.text
