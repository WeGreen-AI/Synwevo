"""Opt-in three-language security ceremonies on an existing loopback test stack.

Run as ``python -m services.identity.tests.browser_security_round --mail-dir ...``.
Creates its own users through public registration/file-mail activation. Chrome's CDP
virtual CTAP2 authenticator is explicitly test equipment, not a physical-device proof.
No request mocking, database writes, exported credentials or external mail.
"""
import argparse
import json
from pathlib import Path
from urllib.parse import urlsplit

import pyotp
from playwright.sync_api import expect, sync_playwright

from .browser_onboarding_round import LABELS, Round


COPY = {
    'en': ['Passkeys', 'Two-step verification', 'Sign out', 'Sign in with a security key',
           'Enable authenticator', 'Confirm', 'Confirm enable', 'Verify and sign in',
           'Use a recovery code', 'I have saved them'],
    'zh-Hans': ['安全密钥（Passkey）', '两步验证', '退出登录', '使用安全密钥登录',
                '启用动态验证码', '确认', '确认启用', '验证并登录', '使用恢复码', '我已保存'],
    'zh-Hant': ['安全金鑰（Passkey）', '兩步驗證', '登出', '使用安全金鑰登入',
                '啟用動態驗證碼', '確認', '確認啟用', '驗證並登入', '使用復原碼', '我已儲存'],
}


def security(round_, browser, locale):
    actor = round_.register(browser, locale)
    page, context = actor['page'], actor['context']
    origin = round_.args.accounts
    labels = COPY[locale]
    # Finish the product's required first-use flow before asserting an /account return.
    round_.portal_entry(actor, lambda: round_.capture_json(page, '/api/auth/onboarding/personal',
        lambda: page.get_by_role('button', name=LABELS[locale]['personal'], exact=True).click()))
    round_.stage = locale + ': virtual Passkey registration and login'
    cdp = context.new_cdp_session(page)
    cdp.send('WebAuthn.enable')
    cdp.send('WebAuthn.addVirtualAuthenticator', {'options': {
        'protocol': 'ctap2', 'transport': 'internal', 'hasResidentKey': True,
        'hasUserVerification': True, 'isUserVerified': True,
        'automaticPresenceSimulation': True}})

    def account():
        page.goto(origin + '/account')
        expect(page.locator('#profile')).to_be_visible()
        expect(page.locator('html')).to_have_attribute('lang', locale)

    def logout():
        page.locator('header.topbar').get_by_role('button', name=labels[2], exact=True).click()
        page.wait_for_url(lambda url: urlsplit(str(url)).path in ('/login', '/logged-out'))
        assert context.request.get(origin + '/api/auth/me').status == 401

    account()
    keys = page.locator('#security > section').filter(has=page.get_by_role('heading', name=labels[0], exact=True))
    keys.locator('input[type=password]').fill(actor['password'])
    keys.locator('button.primary').click()
    expect(keys.locator('tbody tr')).to_have_count(1)
    listing = context.request.get(origin + '/api/auth/passkeys').json()['items']
    assert len(listing) == 1 and listing[0]['usable_here']
    renamed = 'Synthetic virtual device ' + locale
    with page.expect_response(lambda response: response.request.method == 'PATCH' and
                              urlsplit(response.url).path.startswith('/api/auth/passkeys/')) as saved:
        keys.locator('tbody input').fill(renamed)
        keys.locator('tbody input').press('Tab')
    assert saved.value.status == 200
    assert context.request.get(origin + '/api/auth/passkeys').json()['items'][0]['name'] == renamed
    logout()
    page.goto(origin + '/login?next=%2Faccount')
    page.get_by_role('button', name=labels[3], exact=True).click()
    page.wait_for_url('**/account')
    assert round_.me(actor)['session']['amr'] == 'passkey_uv'
    keys.locator('input[type=password]').fill(actor['password'])
    keys.locator('tbody button').click()
    expect(keys.locator('tbody tr')).to_have_count(0)
    assert context.request.get(origin + '/api/auth/passkeys').json()['items'] == []
    round_.record(locale + ': virtual UV Passkey registered, renamed, used for actual sign-in, and removed with reauthentication')

    round_.stage = locale + ': TOTP setup and password second factor'
    mfa = page.locator('#security > section').filter(has=page.get_by_role('heading', name=labels[1], exact=True))
    with page.expect_response('**/api/auth/mfa/totp/begin') as setup:
        mfa.get_by_role('button', name=labels[4], exact=True).click()
    assert setup.value.status in (200, 403)
    if setup.value.status == 403:
        expect(mfa.locator('input[type=password]')).to_be_visible()
        mfa.locator('input[type=password]').fill(actor['password'])
        mfa.get_by_role('button', name=labels[5], exact=True).click()
        expect(mfa.locator('input[type=password]')).to_have_count(0)
        mfa.get_by_role('button', name=labels[4], exact=True).click()
    expect(mfa.locator('.seedbox')).to_be_visible()
    seed = mfa.locator('.seedbox').inner_text()
    round_.hidden.add(seed)
    totp = pyotp.TOTP(seed)
    invalid = '111111' if totp.now() != '111111' else '222222'
    mfa.locator('input[autocomplete=one-time-code]').fill(invalid)
    with page.expect_response('**/api/auth/mfa/totp/confirm') as refused:
        mfa.get_by_role('button', name=labels[6], exact=True).click()
    assert refused.value.status == 400
    assert context.request.get(origin + '/api/auth/mfa').json()['totp'] is False
    enrolled_code = totp.now()
    mfa.locator('input[autocomplete=one-time-code]').fill(enrolled_code)
    mfa.get_by_role('button', name=labels[6], exact=True).click()
    expect(mfa.locator('.reccodes li')).to_have_count(10)
    codes = mfa.locator('.reccodes li').all_inner_texts()
    round_.hidden.update(codes)
    mfa.get_by_role('button', name=labels[9], exact=True).click()
    expect(mfa.locator('.reccodes')).to_have_count(0)
    logout()
    round_.login(actor, '/account')
    expect(page.locator('#login-code')).to_be_visible()
    assert context.request.get(origin + '/api/auth/me').status == 401
    page.locator('#login-code').fill(invalid)
    with page.expect_response('**/api/auth/login/mfa') as bad:
        page.get_by_role('button', name=labels[7], exact=True).click()
    assert bad.value.status == 401
    assert context.request.get(origin + '/api/auth/me').status == 401
    while totp.now() == enrolled_code:
        page.wait_for_timeout(200)  # Real wall-clock TOTP step, no clock manipulation.
    page.locator('#login-code').fill(totp.now())
    page.get_by_role('button', name=labels[7], exact=True).click()
    page.wait_for_url('**/account')
    assert round_.me(actor)['session']['amr'] == 'password+totp'
    round_.record(locale + ': TOTP activation rejects a bad code; password alone cannot sign in; real current TOTP succeeds')

    round_.stage = locale + ': recovery-code single consumption'
    for attempt in range(2):
        logout()
        round_.login(actor, '/account')
        page.get_by_role('button', name=labels[8], exact=True).click()
        page.locator('#login-code').fill(codes[0])
        with page.expect_response('**/api/auth/login/mfa') as recovered:
            page.get_by_role('button', name=labels[7], exact=True).click()
        assert recovered.value.status == (200 if attempt == 0 else 401)
        if attempt:
            assert context.request.get(origin + '/api/auth/me').status == 401
            page.locator('#login-code').fill(codes[1])
            page.get_by_role('button', name=labels[7], exact=True).click()
        page.wait_for_url('**/account')
    assert context.request.get(origin + '/api/auth/mfa').json()['recovery_remaining'] == 8
    page.locator('#security').screenshot(path=str(round_.private / ('security-' + locale + '.png')),
                                       mask=[page.locator('#security input')], animations='disabled')
    round_.record(locale + ': recovery code works once, reuse is rejected, separate unused code succeeds; 8 remain')
    context.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--accounts', default='http://accounts.localhost:29770')
    parser.add_argument('--portal', default='http://portal.localhost:29769')
    parser.add_argument('--docs', default='http://docweave.localhost:29765')
    parser.add_argument('--mail-dir', type=Path, required=True)
    parser.add_argument('--output-dir', type=Path)
    round_ = Round(parser.parse_args())
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(channel='chrome', headless=True)
            probe = browser.new_context()
            for origin in round_.origins:
                assert probe.request.get(origin + '/healthz').json().get('instance') == 'test'
            probe.close()
            for locale in COPY:
                security(round_, browser, locale)
            assert not round_.errors
            browser.close()
    except Exception as error:
        round_.failures.append({'stage': round_.stage, 'error': round_.redact(error)})
    report = {'facility': 'Chrome virtual CTAP2 + real local TOTP; synthetic public registrations and file mail',
              'checks': round_.checks, 'failures': round_.failures, 'page_errors': round_.errors}
    target = round_.private / 'report.json'
    target.write_text(json.dumps(report, ensure_ascii=False, indent=2))
    target.chmod(0o600)
    print(json.dumps(report, ensure_ascii=False), flush=True)
    return int(bool(round_.failures))


if __name__ == '__main__':
    raise SystemExit(main())
