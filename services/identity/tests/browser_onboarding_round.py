"""Real-browser public-auth / organization acceptance against the existing local test stack.

Run from the repository root after building @synwevo/account-web:
  .venv/bin/python -m services.identity.tests.browser_onboarding_round \
    --accounts http://accounts.localhost:29770 \
    --portal http://portal.localhost:29769 \
    --docs http://docweave.localhost:29765 \
    --mail-dir <private-file-mail-directory-for-the-new-test-instance> \
    --output-dir /private/tmp/synwevo-platform-round/task-f-browser

This script NEVER starts/restarts servers, edits the database, relaxes origins, mocks or
rewrites API responses, or uses existing accounts. All mutations use unique synthetic fixture accounts.
The stack must report instance=test on all three origins. Mail links stay in process memory;
private temporary screenshots/report omit credentials, cookies and bearer tokens. Existing
HTTP negative tests remain unchanged. A failed run exits nonzero with a sanitized report.
"""
from __future__ import annotations

import argparse
import binascii
from email import policy
from email.parser import Parser
from email.utils import parseaddr
import json
import os
from pathlib import Path
import re
import secrets
import struct
import tempfile
import time
from urllib.parse import parse_qs, urlsplit
import uuid
import zlib

from playwright.sync_api import sync_playwright, expect

# Observe real browser fetch responses before application code can navigate away. Awaiting
# the binding copies the clone into Python memory before resolving the original Response;
# requests, cookies, headers, status, body and the application's response stay unchanged.
# Do not use page Response.json() after a click: Chrome may have discarded that document.
JSON_CAPTURE_SCRIPT = r"""
(() => {
    const accountOrigin = __ACCOUNT_ORIGIN__;
    const originalFetch = globalThis.fetch.bind(globalThis);
    globalThis.fetch = async (...args) => {
        const response = await originalFetch(...args);
        const url = new URL(response.url);
        const request = args[0] instanceof Request ? args[0] : null;
        const method = String(args[1]?.method ?? request?.method ?? 'GET').toUpperCase();
        const watched = ['/api/auth/register', '/api/auth/email/verify',
            '/api/auth/organizations', '/api/auth/onboarding/personal'].includes(url.pathname)
            || /^\/api\/auth\/organizations\/[^/]+\/workspaces$/.test(url.pathname);
        if (method === 'POST' && url.origin === accountOrigin && watched) {
            let data = null;
            let jsonError = false;
            try { data = await response.clone().json(); } catch { jsonError = true; }
            const headers = new Headers(args[1]?.headers ?? request?.headers);
            await globalThis.__synwevoFixtureResponse({
                path: url.pathname, status: response.status, data, jsonError,
                idempotencyKey: headers.get('Idempotency-Key'),
            });
        }
        return response;
    };
})();
"""

LABELS = {
    'en': {
        'register': 'Create account and verify email', 'verify': 'Verify this email',
        'verified': 'Email verified', 'signin': 'Sign in', 'continue': 'Continue',
        'activate': 'Set new password and verify email', 'signin_continue': 'Sign in and continue',
        'setup': 'Set up this deployment’s first administrator', 'back': 'Back to ordinary sign-in',
        'org_create': 'Create organization and workspace', 'org_show': 'Create organization',
        'members': 'Members and invitations', 'accept': 'Accept invitation and join',
        'decline': 'Decline invitation', 'decline_confirm': 'Confirm decline',
        'declined': 'Invitation declined', 'invited_register': 'Create account and continue',
        'invited_submit': 'Create account and accept invitation', 'personal': 'Use personal workspace',
        'new_space': 'Create workspace', 'open_space': 'Open workspace',
        'avatar_remove': 'Remove organization avatar',
        'confirm_action': 'Confirm action', 'reauth_confirm': 'Verify and confirm',
    },
    'zh-Hans': {
        'register': '创建账户并验证邮箱', 'verify': '验证此邮箱', 'verified': '邮箱已验证',
        'signin': '登录', 'continue': '继续', 'setup': '设置此部署的首位管理员', 'back': '返回普通登录',
        'activate': '设置新口令并验证邮箱', 'signin_continue': '登录并继续',
        'org_create': '创建组织和工作空间', 'org_show': '创建组织', 'members': '成员与邀请',
        'accept': '接受邀请并加入', 'decline': '拒绝邀请', 'decline_confirm': '确认拒绝邀请',
        'declined': '邀请已拒绝', 'invited_register': '创建账户并继续',
        'invited_submit': '创建账户并接受邀请', 'personal': '使用个人工作空间',
        'new_space': '创建工作空间', 'open_space': '进入工作空间', 'avatar_remove': '移除组织头像',
        'confirm_action': '确认操作', 'reauth_confirm': '验证并确认',
    },
    'zh-Hant': {
        'register': '建立帳戶並驗證電郵', 'verify': '驗證此電郵', 'verified': '電郵已驗證',
        'signin': '登入', 'continue': '繼續', 'setup': '設定此部署的首位管理員', 'back': '返回一般登入',
        'activate': '設定新密碼並驗證電郵', 'signin_continue': '登入並繼續',
        'org_create': '建立組織和工作空間', 'org_show': '建立組織', 'members': '成員與邀請',
        'accept': '接受邀請並加入', 'decline': '拒絕邀請', 'decline_confirm': '確認拒絕邀請',
        'declined': '邀請已拒絕', 'invited_register': '建立帳戶並繼續',
        'invited_submit': '建立帳戶並接受邀請', 'personal': '使用個人工作空間',
        'new_space': '建立工作空間', 'open_space': '進入工作空間', 'avatar_remove': '移除組織頭像',
        'confirm_action': '確認操作', 'reauth_confirm': '驗證並確認',
    },
}


def png_fixture() -> bytes:
    def chunk(kind, data):
        return struct.pack('>I', len(data)) + kind + data + struct.pack('>I', binascii.crc32(kind + data) & 0xffffffff)
    return (b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', struct.pack('>IIBBBBB', 1, 1, 8, 2, 0, 0, 0))
            + chunk(b'IDAT', zlib.compress(b'\x00\x48\x91\x77')) + chunk(b'IEND', b''))


class Round:
    def __init__(self, args):
        self.args = args
        self.run_id = uuid.uuid4().hex[:10]
        if args.output_dir:
            self.private = args.output_dir.resolve()
            assert any(self.private.is_relative_to(Path(p).resolve()) for p in ('/tmp', tempfile.gettempdir())), 'Artifacts must be in a private temporary directory'
            self.private.mkdir(mode=0o700, parents=True, exist_ok=False)
        else:
            self.private = Path(tempfile.mkdtemp(prefix='synwevo-onboarding-browser-'))
        self.private.chmod(0o700)
        self.mail_dir = args.mail_dir.resolve(strict=True)
        assert any(self.mail_dir.is_relative_to(Path(p).resolve()) for p in ('/tmp', tempfile.gettempdir())), 'Mail must be in a private temporary directory'
        assert self.mail_dir.stat().st_mode & 0o077 == 0, 'Mail directory is not private'
        self.checks, self.errors, self.failures, self.requests = [], [], [], []
        self.captured_json = []  # In-memory only. Never include API payloads in reports.
        self.hidden = set()
        self.actors = {}
        self.stage = 'preflight'
        self.allowed_hosts = {urlsplit(url).hostname for url in self.origins}
        for origin in self.origins:
            u = urlsplit(origin)
            assert u.scheme == 'http' and (u.hostname in ('localhost', '127.0.0.1', '::1') or u.hostname.endswith('.localhost')), 'Loopback origins required'

    @property
    def origins(self):
        return (self.args.accounts, self.args.portal, self.args.docs)

    def redact(self, value):
        text = str(value)
        for secret in sorted(self.hidden, key=len, reverse=True):
            if secret:
                text = text.replace(secret, '[REDACTED]')
        return re.sub(r'([?#&](?:token|invite_token)=)[^\s&#"<>]+', r'\1[REDACTED]', text)

    def record(self, description):
        self.checks.append(description)
        print('PASS ' + description, flush=True)

    def watch(self, context):
        def observed(request):
            parsed = urlsplit(request.url)
            assert parsed.hostname in self.allowed_hosts or parsed.scheme in ('blob', 'data', 'about'), 'Browser attempted a nonlocal request'
            # Store only route/method; never request bodies, cookies, or query values.
            self.requests.append((request.method, parsed.path, 'token' in parse_qs(parsed.query) or 'invite_token' in parse_qs(parsed.query)))
        context.on('request', observed)
        context.on('page', lambda page: page.on('pageerror', lambda error: self.errors.append(self.redact(error))))

    def capture_json(self, page, path, trigger):
        """Click the real UI and return a response copied before any app navigation."""
        start = len(self.captured_json)
        trigger()
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            for source_page, payload in self.captured_json[start:]:
                if source_page == page and payload['path'] == path:
                    assert not payload['jsonError'], f'Expected a JSON response from {path}'
                    return payload
            page.wait_for_timeout(25)  # Pump the binding callback; no server polling/retry.
        raise AssertionError(f'No captured response for {path}')

    def language(self, page, locale):
        menu = page.locator('.language-menu select').first
        expect(menu).to_be_visible()
        menu.select_option(locale)
        expect(page.locator('html')).to_have_attribute('lang', locale)
        expect(menu).to_have_value(locale)

    def me(self, actor):
        response = actor['context'].request.get(self.args.accounts + '/api/auth/me')
        assert response.status == 200, 'Fixture account session is unavailable'
        data = response.json()
        assert data['email'] == actor['email'] and actor['email'].startswith('f-round-' + self.run_id), 'Session is not this run’s synthetic fixture'
        return data

    def portal_entry(self, actor, trigger, workspace_id=None):
        """Assert the UI's own SSO handoff; never navigate around a broken entry button."""
        page = actor['page']
        with page.expect_request(lambda request: request.is_navigation_request()
                                 and urlsplit(request.url).netloc == urlsplit(self.args.portal).netloc
                                 and urlsplit(request.url).path == '/auth/start') as started:
            result = trigger()
        if workspace_id is None:
            assert result['status'] == 200, 'Personal onboarding failed'
            workspace_id = result['data']['workspace']['id']
        assert started.value.method == 'GET'
        assert parse_qs(urlsplit(started.value.url).query) == {
            'next': ['/workspace'], 'workspace_id': [workspace_id], 'lang': [actor['locale']]}, 'Portal SSO lost explicit workspace or locale'
        page.wait_for_url(re.compile(r'^' + re.escape(self.args.portal) + r'/workspace(?:[?#].*)?$'))
        expect(page.locator('.pt-space.is-current')).to_be_visible()
        response = actor['context'].request.get(self.args.portal + '/api/auth/me')
        assert response.status == 200, 'Portal RP session was not established'
        user = response.json()
        assert user['email'] == actor['email'] and user['workspace_id'] == workspace_id, 'Portal selected another identity or workspace'
        selected = next(w for w in user['workspaces'] if w['id'] == workspace_id)
        expect(page.locator('.pt-space.is-current')).to_contain_text(selected['name'])
        expect(page.locator('html')).to_have_attribute('lang', actor['locale'])
        return result

    def link(self, email, path):
        assert email.startswith('f-round-' + self.run_id), 'Refusing another account’s mail'
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            for file in sorted(self.mail_dir.glob('*.eml'), key=lambda p: p.stat().st_mtime, reverse=True):
                message = Parser(policy=policy.default).parsestr(file.read_text())
                if parseaddr(message.get('To', ''))[1] != email:
                    continue
                body = message.get_body(preferencelist=('plain',))
                text = body.get_content() if body else ''
                for match in re.finditer(r'https?://[^\s<>]+', text):
                    link = match[0]
                    parsed = urlsplit(link)
                    if parsed.path != path:
                        continue
                    assert parsed.netloc == urlsplit(self.args.accounts).netloc, 'Mail link origin differs from configured accounts origin'
                    token = (parse_qs(parsed.fragment).get('token') or parse_qs(parsed.query).get('token') or [None])[0]
                    assert token, 'Fixture mail contains no token'
                    self.hidden.add(token)
                    # New links must be fragments; legacy links are tested separately in HTTP/component tests.
                    query = parse_qs(parsed.query)
                    assert set(query) <= {'lang'} and parsed.fragment, 'New mail link exposes unsupported query fields or has no fragment'
                    assert not query or query['lang'] in (['en'], ['zh-Hans'], ['zh-Hant']), 'Mail locale hint is not one supported language'
                    return link, token
            time.sleep(.2)
        raise AssertionError('No private file-mail link for the current synthetic fixture')

    def actor(self, browser, locale, suffix=None):
        label = suffix or locale.lower()
        context = browser.new_context(locale={'en': 'en-US', 'zh-Hans': 'zh-CN', 'zh-Hant': 'zh-TW'}[locale],
                                      viewport={'width': 1440, 'height': 1050}, reduced_motion='reduce')
        context.expose_binding('__synwevoFixtureResponse',
                               lambda source, payload: self.captured_json.append((source['page'], payload)))
        context.add_init_script(JSON_CAPTURE_SCRIPT.replace('__ACCOUNT_ORIGIN__', json.dumps(self.args.accounts)))
        self.watch(context)
        password = 'Independent lunar rivers ' + secrets.token_hex(12) + '!'
        self.hidden.add(password)
        return {'context': context, 'page': context.new_page(), 'locale': locale, 'password': password,
                'email': f'f-round-{self.run_id}-{label}@example.test'}

    def login(self, actor, next_path='/onboarding'):
        from urllib.parse import quote
        page, locale = actor['page'], actor['locale']
        page.goto(self.args.accounts + '/login?next=' + quote(next_path, safe=''))
        self.language(page, locale)
        # Regression: bootstrap_required must not unconditionally replace the ordinary form.
        expect(page.locator('#bootstrap-token')).to_have_count(0)
        expect(page.get_by_role('button', name=LABELS[locale]['signin'], exact=True)).to_be_visible()
        state = actor['context'].request.get(self.args.accounts + '/api/auth/state').json()
        if state.get('bootstrap_required'):
            page.get_by_role('button', name=LABELS[locale]['setup'], exact=True).click()
            expect(page.locator('#bootstrap-token')).to_be_visible()
            page.get_by_role('button', name=LABELS[locale]['back'], exact=True).click()
            expect(page.locator('#bootstrap-token')).to_have_count(0)
            expect(page.get_by_role('button', name=LABELS[locale]['signin'], exact=True)).to_be_visible()
        page.locator('#login-email').fill(actor['email'])
        page.locator('input[autocomplete=current-password]').fill(actor['password'])
        page.get_by_role('button', name=LABELS[locale]['signin'], exact=True).click()

    def register(self, browser, locale):
        actor = self.actor(browser, locale)
        page = actor['page']
        self.stage = f'{locale}: portal → registration'
        page.goto(self.args.portal + '/')
        self.language(page, locale)
        page.locator('a.pt-nav-register').click()
        page.wait_for_url('**/register*')
        expect(page.locator('html')).to_have_attribute('lang', locale)
        expect(page.locator('#register-name')).to_be_visible()
        assert 'lang=' not in page.url, 'Navigation locale hint was not consumed'
        page.locator('#register-name').fill(f'Fixture {self.run_id} {locale}')
        page.locator('#register-email').fill(actor['email'])
        page.locator('input[autocomplete=new-password]').fill(actor['password'])
        # Locale changes must retain entered values and the same input DOM node.
        page.locator('#register-email').evaluate('el => { window.fixtureEmailNode = el; }')
        other = 'zh-Hant' if locale != 'zh-Hant' else 'en'
        self.language(page, other)
        expect(page.locator('#register-email')).to_have_value(actor['email'])
        assert page.locator('#register-email').evaluate('el => el === window.fixtureEmailNode')
        self.language(page, locale)
        page.locator('input[name=intent][value=organization]').check()
        registered = self.capture_json(page, '/api/auth/register',
            lambda: page.get_by_role('button', name=LABELS[locale]['register'], exact=True).click())
        assert registered['status'] == 202
        assert registered['data']['delivery'] == 'requested', 'File-mail registration should acknowledge a request, not delivery'
        page.wait_for_url('**/verify-email*')
        self.record(f'{locale}: portal language handoff, retained inputs and real registration 202')

        self.stage = f'{locale}: pending login / bootstrap regression'
        self.login(actor)
        page.wait_for_url('**/verify-email*')
        pending = self.me(actor)
        assert pending['status'] == 'pending' and pending['email_verified'] is False and not pending['workspaces']
        assert actor['context'].request.get(self.args.accounts + '/api/auth/onboarding').status == 403
        self.record(f'{locale}: ordinary login works before bootstrap; pending account cannot access onboarding')

        self.stage = f'{locale}: explicit email verification'
        link, _ = self.link(actor['email'], '/verify-email')
        before = len([r for r in self.requests if r[:2] == ('POST', '/api/auth/email/verify')])
        # Exercise the actual locale-bearing mail URL, then the same-document fragment
        # navigation reported by browser QA, which cannot be covered by a full reload.
        page.goto(link)
        expect(page.get_by_role('button', name=LABELS[locale]['verify'], exact=True)).to_be_visible()
        expect(page.locator('html')).to_have_attribute('lang', locale)
        page.goto(self.args.accounts + '/verify-email')
        expect(page.locator('#verify-email')).to_be_visible()
        expect(page.get_by_role('button', name=LABELS[locale]['verify'], exact=True)).to_have_count(0)
        page.evaluate('window.fixtureSameDocument = true')
        page.goto(urlsplit(link)._replace(query='').geturl())
        assert page.evaluate('window.fixtureSameDocument === true'), 'Verification link did not exercise same-document hash navigation'
        expect(page.get_by_role('button', name=LABELS[locale]['verify'], exact=True)).to_be_visible()
        page.wait_for_function('!location.hash && !location.search')
        assert len([r for r in self.requests if r[:2] == ('POST', '/api/auth/email/verify')]) == before
        assert self.me(actor)['email_verified'] is False
        proof = self.capture_json(page, '/api/auth/email/verify',
            lambda: page.get_by_role('button', name=LABELS[locale]['verify'], exact=True).click())
        assert proof['status'] == 400 and proof['data']['error'] == 'new_password_required'
        assert self.me(actor)['status'] == 'pending', 'Token-only confirmation activated a pre-registered password'
        password = page.locator('input[autocomplete=new-password]')
        expect(password).to_be_focused()
        previous_password = actor['password']
        replacement = 'Mailbox chosen secret rivers ' + secrets.token_hex(12) + '!'
        self.hidden.add(replacement)
        password.fill(replacement)
        other = 'zh-Hant' if locale != 'zh-Hant' else 'en'
        self.language(page, other)
        expect(password).to_have_value(replacement)
        self.language(page, locale)
        activated = self.capture_json(page, '/api/auth/email/verify',
            lambda: page.get_by_role('button', name=LABELS[locale]['activate'], exact=True).click())
        assert activated['status'] == 200 and activated['data']['sign_in_required'] is True
        expect(page.get_by_role('heading', name=LABELS[locale]['verified'], exact=True)).to_be_visible()
        assert actor['context'].request.get(self.args.accounts + '/api/auth/me').status == 401, 'Pending session survived account activation'
        stale = actor['context'].request.post(self.args.accounts + '/api/auth/login',
                    data={'email': actor['email'], 'password': previous_password}, headers={'Origin': self.args.accounts})
        assert stale.status == 401, 'Pre-registration password still works after activation'
        expect(page.get_by_role('link', name=LABELS[locale]['signin_continue'], exact=True)).to_be_visible()
        actor['password'] = replacement
        self.login(actor)
        page.wait_for_url('**/onboarding')
        assert self.me(actor)['email_verified'] is True
        self.record(f'{locale}: explicit mailbox activation requires a new password, revokes pending sessions/old password and reaches onboarding after sign-in')
        self.actors[locale] = actor
        return actor

    def organization(self, actor):
        page, locale = actor['page'], actor['locale']
        self.stage = f'{locale}: organization creation'
        if not page.locator('#org-name').is_visible():
            page.get_by_role('button', name=LABELS[locale]['org_show'], exact=True).click()
        name = f'Fixture organization {self.run_id} {locale}'
        workspace = f'Fixture workspace {self.run_id} {locale}'
        page.locator('#org-name').fill(name)
        page.locator('#org-workspace').fill(workspace)
        created = self.capture_json(page, '/api/auth/organizations',
            lambda: page.get_by_role('button', name=LABELS[locale]['org_create'], exact=True).click())
        assert created['status'] == 201
        result = created['data']
        assert created['idempotencyKey'], 'Organization create omitted retry key'
        actor['org_id'], actor['workspace_id'] = result['organization']['id'], result['workspace']['id']
        actor['org_name'], actor['workspace_name'] = name, workspace
        page.wait_for_url('**/organizations/' + actor['org_id'])
        expect(page.get_by_role('heading', name=name, exact=True)).to_be_visible()
        assert not any(w['kind'] == 'personal' for w in self.me(actor)['workspaces']), 'Organization onboarding created an unrelated personal workspace'
        assert page.locator('a[href^="/admin"]').count() == 0
        page.set_viewport_size({'width': 390, 'height': 844})
        assert page.evaluate('document.documentElement.scrollWidth <= innerWidth'), 'Organization UI overflows mobile viewport'
        page.screenshot(path=str(self.private / f'organizations-{locale}-mobile.png'), full_page=True)
        page.set_viewport_size({'width': 1440, 'height': 1050})
        page.reload()
        expect(page.locator('html')).to_have_attribute('lang', locale)
        expect(page.get_by_role('heading', name=name, exact=True)).to_be_visible()
        self.record(f'{locale}: organization/workspace created, reload persists locale, mobile layout fits and no platform-admin controls')

    def confirm_org_action(self, actor):
        page = actor['page']
        dialog = page.get_by_role('alertdialog')
        labels = LABELS[actor['locale']]
        expect(dialog).to_be_visible()
        dialog.locator('textarea').fill('Synthetic browser fixture verification')
        # The real form uses a native button's default submit behavior; its DOM has no
        # type attribute. Match the accessible action name, not an optional HTML attribute.
        confirm = dialog.get_by_role('button', name=labels['confirm_action'], exact=True)
        expect(confirm).to_be_enabled()
        confirm.click()
        # First request may ask for strict admin reauthentication. Do not bypass it.
        deadline = time.monotonic() + 12
        while time.monotonic() < deadline:
            if not dialog.count():
                return
            password = dialog.locator('input[autocomplete=current-password]')
            if password.count():
                password.fill(actor['password'])
                confirm = dialog.get_by_role('button', name=labels['reauth_confirm'], exact=True)
                expect(confirm).to_be_enabled()
                confirm.click()
                expect(dialog).to_have_count(0)
                return
            page.wait_for_timeout(100)
        raise AssertionError('Organization action did not complete or request reauthentication')

    def avatar(self, actor):
        page = actor['page']
        self.stage = 'organization avatar validation / upload / remove'
        page.locator('input[type=file][accept="image/png"]').set_input_files({'name': 'fixture-invalid.png', 'mimeType': 'image/png', 'buffer': b'not-a-png'})
        expect(page.get_by_role('alert')).to_contain_text('PNG')
        with page.expect_response(lambda r: urlsplit(r.url).path.endswith('/avatar') and r.request.method == 'PUT') as first_attempt:
            page.locator('input[type=file][accept="image/png"]').set_input_files({'name': 'fixture-avatar.png', 'mimeType': 'image/png', 'buffer': png_fixture()})
            self.confirm_org_action(actor)
        assert first_attempt.value.status in (200, 403)  # 403 triggers the explicit password ceremony above.
        image = page.locator('.org-avatar img')
        expect(image).to_be_visible()
        expect(image).to_have_js_property('naturalWidth', 1)
        page.reload()
        expect(page.locator('.org-avatar img')).to_be_visible()
        page.screenshot(path=str(self.private / 'organization-avatar-en.png'), full_page=True)
        page.get_by_role('button', name=LABELS[actor['locale']]['avatar_remove'], exact=True).click()
        self.confirm_org_action(actor)
        expect(page.locator('.org-avatar img')).to_have_count(0)
        self.record('Organization avatar: invalid PNG rejected, real upload/reauth persisted, removal completed')

    def invitation(self, owner, email):
        page = owner['page']
        page.goto(self.args.accounts + f"/account/workspaces/{owner['workspace_id']}/members?tab=invites")
        self.language(page, 'en')
        page.get_by_role('button', name='Invite members', exact=True).click()
        dialog = page.get_by_role('alertdialog')
        dialog.locator('textarea').fill(email)
        with page.expect_response(lambda r: urlsplit(r.url).path.endswith('/invitations/batch') and r.request.method == 'POST') as result:
            dialog.get_by_role('button', name='Send invitations', exact=True).click()
        assert result.value.status == 200, 'UI invitation request failed'
        return self.link(email, '/invite')

    def invite_flows(self, browser):
        owner, member, decliner = self.actors['en'], self.actors['zh-Hant'], self.actors['zh-Hans']
        self.stage = 'existing-account invitation acceptance'
        link, _ = self.invitation(owner, member['email'])
        page = member['page']
        before = len([r for r in self.requests if r[:2] == ('POST', '/api/auth/invitations/accept')])
        page.goto(link)
        expect(page.get_by_role('button', name=LABELS['zh-Hant']['accept'], exact=True)).to_be_visible()
        page.wait_for_function('!location.hash && !location.search')
        assert len([r for r in self.requests if r[:2] == ('POST', '/api/auth/invitations/accept')]) == before
        page.get_by_role('button', name=LABELS['zh-Hant']['accept'], exact=True).click()
        page.wait_for_url('**/onboarding')
        assert any(w['id'] == owner['workspace_id'] for w in self.me(member)['workspaces'])
        page.goto(self.args.accounts + '/organizations/' + owner['org_id'])
        expect(page.get_by_role('heading', name=owner['org_name'], exact=True)).to_be_visible()
        assert page.locator('#new-workspace-name').count() == 0
        assert page.locator('input[type=file]').count() == 0
        assert page.locator('a[href^="/admin"]').count() == 0
        assert member['context'].request.get(self.args.accounts + f"/api/auth/organizations/{owner['org_id']}/members").status == 403
        self.record('Invitation acceptance uses explicit POST; ordinary org member sees no administration or avatar mutation controls')

        self.stage = 'invitation decline / idempotency / refusal after decline'
        link, token = self.invitation(owner, decliner['email'])
        page = decliner['page']
        page.goto(link)
        page.get_by_role('button', name=LABELS['zh-Hans']['decline'], exact=True).click()
        page.get_by_role('button', name=LABELS['zh-Hans']['decline_confirm'], exact=True).click()
        expect(page.get_by_role('heading', name=LABELS['zh-Hans']['declined'], exact=True)).to_be_visible()
        assert not any(w['id'] == owner['workspace_id'] for w in self.me(decliner)['workspaces'])
        headers = self.csrf(decliner)
        assert decliner['context'].request.post(self.args.accounts + '/api/auth/invitations/decline', data={'token': token}, headers=headers).status == 200
        refused = decliner['context'].request.post(self.args.accounts + '/api/auth/invitations/accept', data={'token': token}, headers=headers)
        assert refused.status == 409 and refused.json()['error'] == 'invite_declined'
        self.record('Invitation decline is confirmed in UI, creates no membership, is idempotent and prevents acceptance')

        self.stage = 'new-account invitation registration'
        invited = self.actor(browser, 'en', 'invited-new')
        link, _ = self.invitation(owner, invited['email'])
        page = invited['page']
        page.goto(link)
        page.get_by_role('link', name=LABELS['en']['invited_register'], exact=True).click()
        expect(page.locator('#register-email')).to_have_value(invited['email'])
        expect(page.locator('#register-email')).to_have_attribute('readonly', '')
        page.locator('#register-name').fill('Invited fixture')
        page.locator('input[autocomplete=new-password]').fill(invited['password'])
        page.get_by_role('button', name=LABELS['en']['invited_submit'], exact=True).click()
        page.wait_for_url('**/onboarding')
        user = self.me(invited)
        assert user['email_verified'] is True and [w['id'] for w in user['workspaces']] == [owner['workspace_id']]
        self.record('New invited account verifies exact email and joins only the invited workspace')
        self.stage = 'explicit personal onboarding / idempotency'
        personal = self.portal_entry(invited, lambda: self.capture_json(page, '/api/auth/onboarding/personal',
            lambda: page.get_by_role('button', name=LABELS['en']['personal'], exact=True).click()))
        assert personal['status'] == 200
        personal_id = personal['data']['workspace']['id']
        spaces = self.me(invited)['workspaces']
        assert len([w for w in spaces if w['kind'] == 'personal']) == 1
        assert any(w['id'] == personal_id for w in spaces)
        page.goto(self.args.accounts + '/onboarding')
        repeated = self.portal_entry(invited, lambda: self.capture_json(page, '/api/auth/onboarding/personal',
            lambda: page.get_by_role('button', name=LABELS['en']['personal'], exact=True).click()), personal_id)
        assert repeated['status'] == 200 and repeated['data']['workspace']['id'] == personal_id
        self.record('Personal onboarding is an explicit UI choice, reaches the selected portal workspace through SSO and reuses it on retry')

    def csrf(self, actor):
        cookies = actor['context'].cookies(self.args.accounts)
        token = next(c['value'] for c in cookies if c['name'] == 'dw_csrf')
        return {'Origin': self.args.accounts, 'X-Synwevo-CSRF': token}

    def workspace_switch(self, actor):
        page = actor['page']
        self.stage = 'workspace selection / portal handoff'
        page.goto(self.args.accounts + '/organizations/' + actor['org_id'])
        second = f'Second fixture space {self.run_id}'
        page.locator('#new-workspace-name').fill(second)
        created = self.capture_json(page, f"/api/auth/organizations/{actor['org_id']}/workspaces",
            lambda: page.get_by_role('button', name=LABELS['en']['new_space'], exact=True).click())
        assert created['status'] == 201
        second_id = created['data']['workspace']['id']
        card = page.locator('.org-card').filter(has=page.get_by_role('heading', name=second, exact=True))
        self.portal_entry(actor, lambda: card.get_by_role('button', name=LABELS['en']['open_space'], exact=True).click(), second_id)
        expect(page.locator('.pt-space.is-current')).to_contain_text(second)
        page.locator('.pt-space').filter(has_text=actor['workspace_name']).click()
        expect(page.locator('.pt-space.is-current')).to_contain_text(actor['workspace_name'])
        page.reload()
        expect(page.locator('.pt-space.is-current')).to_contain_text(actor['workspace_name'])
        for locale in ('zh-Hans', 'zh-Hant', 'en'):
            self.language(page, locale)
            expect(page.locator('.pt-space.is-current')).to_contain_text(actor['workspace_name'])
        portal_me = actor['context'].request.get(self.args.portal + '/api/auth/me').json()
        assert portal_me['workspace_id'] == actor['workspace_id'] and portal_me['workspace_id'] != second_id
        # The same tab already holds the original portal workspace. Both account entry
        # points must replace that context via SSO, without a test-side /workspace redirect.
        page.goto(self.args.accounts + '/onboarding')
        available = page.locator('.org-card').filter(has=page.get_by_role('heading', name=second, exact=True))
        self.portal_entry(actor, lambda: available.get_by_role('button', name=LABELS['en']['open_space'], exact=True).click(), second_id)
        expect(page.locator('.pt-space.is-current')).to_contain_text(second)
        page.goto(self.args.accounts + '/organizations/' + actor['org_id'])
        original = page.locator('.org-card').filter(has=page.get_by_role('heading', name=actor['workspace_name'], exact=True))
        self.portal_entry(actor, lambda: original.get_by_role('button', name=LABELS['en']['open_space'], exact=True).click(), actor['workspace_id'])
        expect(page.locator('.pt-space.is-current')).to_contain_text(actor['workspace_name'])
        page.screenshot(path=str(self.private / 'portal-workspace-switch-en.png'), full_page=True)
        self.record('Organization and onboarding SSO replace stale portal tab workspace; switching and three languages preserve selection after reload')

    def run(self):
        try:
            with sync_playwright() as pw:
                browser = pw.chromium.launch(channel='chrome', headless=True)
                preflight = browser.new_context()
                for origin in self.origins:
                    response = preflight.request.get(origin + '/healthz')
                    assert response.ok and response.json().get('instance') == 'test', 'Refusing non-test or unavailable stack'
                preflight.close()
                for locale in ('en', 'zh-Hans', 'zh-Hant'):
                    actor = self.register(browser, locale)
                    self.organization(actor)
                self.avatar(self.actors['en'])
                self.invite_flows(browser)
                self.workspace_switch(self.actors['en'])
                assert not self.errors, 'Browser console reported errors'
                assert not any(exposed for _, _, exposed in self.requests), 'A bearer token entered a request URL'
                self.record('No browser errors and no bearer tokens in request URLs throughout the round')
                browser.close()
        except Exception as error:
            self.failures.append({'stage': self.stage, 'error': self.redact(error)})
        report = {'run_id': self.run_id, 'checks': self.checks, 'failures': self.failures,
                  'page_errors': self.errors, 'request_count': len(self.requests), 'artifacts': str(self.private)}
        target = self.private / 'report.json'
        target.write_text(json.dumps(report, ensure_ascii=False, indent=2))
        target.chmod(0o600)
        print(json.dumps(report, ensure_ascii=False, indent=2), flush=True)
        return 1 if self.failures else 0


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--accounts', default='http://accounts.localhost:28770')
    parser.add_argument('--portal', default='http://portal.localhost:28769')
    parser.add_argument('--docs', default='http://docweave.localhost:28765')
    parser.add_argument('--mail-dir', type=Path, required=True)
    parser.add_argument('--output-dir', type=Path, help='New private temporary output directory (must not already exist)')
    args = parser.parse_args()
    raise SystemExit(Round(args).run())


if __name__ == '__main__':
    main()
