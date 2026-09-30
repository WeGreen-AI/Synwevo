"""Real-browser account proof, with temporary users/Store/file mail and loopback only.

First build @synwevo/account-web, then from the repository root run:
    .venv/bin/python -m services.identity.tests.browser_account_review
No API interception or fake login. Screenshots and a compact report remain in /tmp;
the isolated user database is removed on exit. This is an explicit browser test,
not part of the ordinary pytest suite.
"""
from pathlib import Path
import binascii
import json
import os
import re
import socket
import struct
import tempfile
import threading
import time
from urllib.parse import parse_qs, urlsplit
import zlib

import uvicorn
from playwright.sync_api import sync_playwright, expect

PW = 'Temporary browser demonstration passphrase 2026!'


def png():
    def chunk(kind, data):
        return struct.pack('>I', len(data)) + kind + data + struct.pack('>I', binascii.crc32(kind + data) & 0xffffffff)
    return b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', struct.pack('>IIBBBBB', 1, 1, 8, 2, 0, 0, 0)) + chunk(b'IDAT', zlib.compress(b'\x00\x49\x8b\x76')) + chunk(b'IEND', b'')


def run():
    artifacts = Path(tempfile.mkdtemp(prefix='synwevo-account-browser-proof-', dir='/tmp'))
    with tempfile.TemporaryDirectory(prefix='synwevo-account-browser-data-') as tmp, socket.socket() as listener:
        listener.bind(('127.0.0.1', 0))
        listener.listen(128)
        origin = 'http://127.0.0.1:' + str(listener.getsockname()[1])
        for key, value in {'DATA_DIR': tmp, 'UI_AUTH': 'session', 'COOKIE_SECURE': '0', 'TEST_INSTANCE': '1',
                           'ROLE': 'accounts', 'MAIL_MODE': 'file', 'MAIL_DIR': tmp + '/mail',
                           'PUBLIC_URL': origin, 'REGISTRATION': 'open'}.items():
            os.environ['SYNWEVO_' + key] = value
        from synwevo_identity.app import create_app
        app = create_app(tmp, start_jobs=False)
        ident = app.state.identity
        from synwevo_core.platform_access import PlatformAccess, SUPER_ADMIN
        admin = ident.create_user('fixture-admin@example.test', PW, status='active')
        ident.store.execute('UPDATE users SET email_verified_at=? WHERE id=?', (time.time(), admin['id']))
        PlatformAccess(ident.store).host_grant(admin['id'], SUPER_ADMIN, source='fixture:browser-review',
                                               reason='browser review fixture administrator')
        user = ident.create_user('browser-proof@example.test', PW, display_name='Browser proof fixture', status='active')
        ident.store.execute("UPDATE users SET locale='en',timezone='UTC',email_verified_at=? WHERE id=?", (time.time(), user['id']))
        server = uvicorn.Server(uvicorn.Config(app, log_level='error', access_log=False))
        worker = threading.Thread(target=lambda: server.run(sockets=[listener]), daemon=True)
        worker.start()
        try:
            for _ in range(100):
                if server.started:
                    break
                time.sleep(.05)
            assert server.started, 'Isolated loopback account server failed to start'
            with sync_playwright() as playwright:
                browser = playwright.chromium.launch(channel='chrome', headless=True)
                context = browser.new_context(locale='en-US', viewport={'width': 1440, 'height': 1100})
                page = context.new_page()
                requests, errors, checks = [], [], []
                context.on('request', lambda request: requests.append((request.method, request.url)))
                page.on('pageerror', lambda error: errors.append(str(error)))
                assert context.request.get(origin + '/healthz').json()['instance'] == 'test'

                def login(email):
                    page.goto(origin + '/login?next=/account')
                    page.locator('#login-email').fill(email)
                    page.locator('input[type=password]').fill(PW)
                    page.locator('form button.primary').click()
                    page.wait_for_url(origin + '/account')
                    expect(page.locator('#profile')).to_be_visible()

                def mail_link(kind):
                    row = ident.store.one('SELECT body FROM mail_outbox WHERE kind=? ORDER BY created_at DESC LIMIT 1', (kind,))
                    return re.search(re.escape(origin) + r'/\S+', row['body'])[0]

                login(user['email'])
                expect(page.locator('#profile')).to_contain_text('Make your account yours')
                page.screenshot(path=str(artifacts / 'account-en-desktop.png'), full_page=True, animations='disabled')
                profile = page.locator('#profile')
                profile.get_by_label('Display name', exact=True).fill('Personal browser fixture')
                profile.get_by_label('Language', exact=True).select_option('zh-Hant')
                profile.get_by_label('Time zone', exact=True).fill('America/New_York')
                profile.get_by_role('button', name='Save changes', exact=True).click()
                expect(profile).to_contain_text('讓帳戶更像你')
                page.reload()
                expect(page.locator('#profile').get_by_label('時區', exact=True)).to_have_value('America/New_York')
                expect(page.locator('#profile').get_by_label('語言', exact=True)).to_have_value('zh-Hant')
                stored = ident.get_user(user['id'])
                assert stored['locale'] == 'zh-Hant' and stored['timezone'] == 'America/New_York'
                session_at = context.request.get(origin + '/api/auth/sessions').json()['items'][0]['last_seen_at']
                formatted = page.evaluate("at => new Intl.DateTimeFormat('zh-Hant', {dateStyle:'medium', timeStyle:'short', timeZone:'America/New_York'}).format(at * 1000)", session_at)
                expect(page.locator('#devices')).to_contain_text(formatted)
                checks.append('Saved locale and timezone survive reload and format live session timestamps')

                upload = page.locator('#profile input[type=file]')
                upload.set_input_files({'name': 'fixture.png', 'mimeType': 'image/png', 'buffer': png()})
                expect(page.locator('#profile img')).to_be_visible()
                assert ident.get_user(user['id'])['avatar_blob'] == png()
                upload.set_input_files({'name': 'invalid.png', 'mimeType': 'image/png', 'buffer': b'<svg/>'})
                expect(page.locator('#profile [role=alert]')).to_contain_text('一般 PNG 圖片')
                checks.append('Canonical avatar persisted; invalid image displays a translated error')
                page.set_viewport_size({'width': 390, 'height': 844})
                page.screenshot(path=str(artifacts / 'account-zh-Hant-mobile.png'), full_page=True, animations='disabled')
                assert page.evaluate('document.documentElement.scrollWidth <= window.innerWidth'), 'Account page overflows mobile viewport'
                assert page.locator('.account-content > *').evaluate_all('(cards) => cards.every(card => card.getBoundingClientRect().right <= innerWidth)'), 'Account cards overflow the mobile viewport'
                page.set_viewport_size({'width': 1440, 'height': 1100})

                email = page.locator('#email')
                email.locator('input[type=email]').fill('changed-browser@example.test')
                email.locator('input[type=password]').fill(PW)
                email.locator('form button').click()
                expect(email.locator('[role=status]')).to_be_visible()
                link = mail_link('email_change')
                token = parse_qs(urlsplit(link).fragment)['token'][0]
                page.goto(link)
                expect(page.locator('#email')).to_contain_text('請確認身分以完成電子郵件地址變更')
                page.wait_for_function('!location.hash && !location.search')
                checks.append('Already signed-in account captures the same-page email-change fragment')

                # Open the same still-unused mail capability on a different, signed-out device.
                # The login redirect may retain /account, but must never carry the capability.
                recipient_context = browser.new_context(locale='en-US')
                recipient_context.on('request', lambda request: requests.append((request.method, request.url)))
                recipient = recipient_context.new_page()
                recipient.on('pageerror', lambda error: errors.append(str(error)))
                recipient.goto(link)
                recipient.wait_for_url(re.compile(re.escape(origin) + r'/login\?'))
                assert token not in recipient.url
                bridge_key = 'synwevo.account.email-change.pending'
                stored_link = recipient.evaluate('key => JSON.parse(sessionStorage.getItem(key))', bridge_key)
                assert stored_link['token'] == token
                recipient.locator('#login-email').fill(user['email'])
                recipient.locator('input[type=password]').fill(PW)
                recipient.locator('form button.primary').click()
                recipient.wait_for_url(origin + '/account')
                expect(recipient.locator('#email')).to_contain_text('請確認身分以完成電子郵件地址變更')
                assert recipient.evaluate('key => sessionStorage.getItem(key)', bridge_key) is None
                recipient.locator('#email input[type=password]').fill(PW)
                recipient.locator('#email form button').click()
                expect(recipient.locator('#email [role=status]')).to_be_visible()
                assert ident.get_user(user['id'])['email'] == 'changed-browser@example.test'
                assert not any(token in url for _, url in requests)
                assert context.request.get(origin + '/api/auth/me').status == 401
                recipient_context.close()
                checks.append('Signed-out mail link survives login via one-use tab storage, no token in requests; reauthenticated change revokes old sessions')

                login('changed-browser@example.test')
                privacy = page.locator('#privacy')
                privacy.locator('input[type=password]').fill(PW)
                with page.expect_download() as download:
                    privacy.get_by_role('button', name='匯出個人資料', exact=True).click()
                exported = json.loads(Path(download.value.path()).read_text())
                assert exported['profile']['id'] == user['id']
                assert 'fixture-admin@example.test' not in json.dumps(exported)
                privacy.locator('input[type=password]').fill(PW)
                privacy.get_by_role('button', name='申請註銷帳戶', exact=True).click()
                page.get_by_role('button', name='確認申請註銷', exact=True).click()
                expect(page.get_by_text('註銷申請已受理', exact=True)).to_be_visible()
                checks.append('Personal export downloaded; deletion confirmation disabled the account')

                recovery_link = mail_link('account_recovery')
                recovery_token = parse_qs(urlsplit(recovery_link).fragment)['token'][0]
                anonymous = browser.new_context(locale='en-US')
                recovery = anonymous.new_page()
                recovery_requests = []
                anonymous.on('request', lambda request: recovery_requests.append((request.method, request.url)))
                recovery.goto(recovery_link)
                expect(recovery.get_by_role('button', name='取消帳戶註銷', exact=True)).to_be_visible()
                expect(recovery.locator('html')).to_have_attribute('lang', 'zh-Hant')
                recovery.wait_for_function('!location.hash && !location.search')
                assert ('POST', origin + '/api/auth/deletion/recovery/preview') in recovery_requests
                assert not any(recovery_token in url for _, url in recovery_requests)
                recovery.screenshot(path=str(artifacts / 'recovery-zh-Hant.png'), full_page=True, animations='disabled')
                recovery.get_by_role('button', name='取消帳戶註銷', exact=True).click()
                expect(recovery.locator('.authcard [role=status]')).to_contain_text('正常登入')
                assert ident.get_user(user['id'])['status'] == 'active'
                assert anonymous.request.get(origin + '/api/auth/me').status == 401
                checks.append('New-device recovery honors mailed locale, uses POST preview, keeps token out of request URL and issues no login')
                assert not errors, errors
                (artifacts / 'report.json').write_text(json.dumps({'checks': checks, 'page_errors': errors}, indent=2))
                print(json.dumps({'artifacts': str(artifacts), 'checks': checks}, indent=2))
                browser.close()
        finally:
            server.should_exit = True
            worker.join(timeout=5)


if __name__ == '__main__':
    run()
