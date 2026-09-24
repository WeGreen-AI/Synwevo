"""Real i18n/editor browser checks against an isolated Vite fixture; never opens a Store.
Run from repository root: .venv/bin/python apps/docs/web/tests/browser_i18n.py
Every /api request is blocked, no backend is started, and artifacts use a temporary directory.
"""
from pathlib import Path
import json
import base64
import re
import socket
import subprocess
import tempfile
import time
import urllib.request

from playwright.sync_api import sync_playwright, expect

ROOT = Path(__file__).resolve().parents[4]
WEB = ROOT / "apps/docs/web"
with socket.socket() as sock:
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
url = f"http://127.0.0.1:{port}/tests/fixtures/i18n.html"
with tempfile.TemporaryDirectory(prefix="synwevo-i18n-") as tmp:
    log = open(Path(tmp) / "vite.log", "w+")
    proc = subprocess.Popen(["node", str(ROOT / "node_modules/vite/bin/vite.js"), "--host", "127.0.0.1", "--port", str(port), "--strictPort"], cwd=WEB, stdout=log, stderr=log)
    try:
        for _ in range(100):
            try:
                urllib.request.build_opener(urllib.request.ProxyHandler({})).open(url, timeout=1).close()
                break
            except Exception as exc:
                last_error = str(exc)
                if proc.poll() is not None:
                    log.seek(0)
                    raise RuntimeError(log.read())
                time.sleep(.1)
        else:
            log.seek(0)
            raise RuntimeError(f"Vite fixture did not start: {last_error}\n{log.read()}")
        with sync_playwright() as pw:
            browser = pw.chromium.launch(channel="chrome", headless=True)
            context = browser.new_context(locale="en-US")
            api_calls = []
            context.route("**/api/**", lambda route: (api_calls.append(route.request.url), route.abort()))
            page = context.new_page()
            errors = []
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.goto(url)
            language = page.locator('[data-testid="fixture-controls"] select')
            expect(language).to_have_value("en")
            expect(page.locator("html")).to_have_attribute("lang", "en")
            expect(page).to_have_title("Synwevo Docs · Document workspace")
            expect(page.locator("output")).to_have_text("1 project")
            field = page.get_by_test_id("state-input")
            field.fill("用户输入 remains <b>{count}</b>")
            field.evaluate("el => { window.retainedInput = el; el.setSelectionRange(2, 6); }")
            mount = page.locator("body").get_attribute("data-probe-mount")
            values = page.get_by_role("textbox", name="Value map, one per line: old=new")
            values.fill("TRUE=是\nFALSE=否\nunfinished")
            language.select_option("zh-Hant")
            expect(page.locator("html")).to_have_attribute("lang", "zh-Hant")
            expect(field).to_have_value("用户输入 remains <b>{count}</b>")
            assert field.evaluate("el => el === window.retainedInput && el.selectionStart === 2 && el.selectionEnd === 6")
            assert page.locator("body").get_attribute("data-probe-mount") == mount
            expect(page.locator("textarea")).to_have_value("TRUE=是\nFALSE=否\nunfinished")
            assert json.loads(page.get_by_test_id("rules").inner_text())[0]["table"] == {"TRUE": "是", "FALSE": "否"}
            expect(page).to_have_title("Synwevo Docs · 文檔工作台")
            page.reload()
            expect(language).to_have_value("zh-Hant")
            language.select_option("en")
            page.get_by_role("link", name="Document", exact=True).click()
            # Header, body and footer are separate real TipTap instances.
            editable = page.locator('.editor-page .tiptap[contenteditable="true"]')
            expect(editable).to_have_count(3)
            body = editable.nth(1)
            body.click()
            body.fill("用户文档正文 stays unchanged")
            body.evaluate("el => { window.documentNode = el; }")
            language.select_option("zh-Hans")
            expect(body).to_have_text("用户文档正文 stays unchanged")
            assert body.evaluate("el => el === window.documentNode")
            expect(page.get_by_role("status").filter(has_text="未保存")).to_be_visible()
            language.select_option("en")
            page.get_by_role("button", name="Undo", exact=True).click()
            assert body.inner_text().strip() != "用户文档正文 stays unchanged", "Undo history lost across locale switch"
            body.click()
            page.get_by_role('button', name='＋ Insert field', exact=True).click()
            page.get_by_role('textbox', name='Field name (replaced by data during generation)').fill('客户字段 unchanged')
            page.get_by_role('dialog').get_by_role('button', name='Confirm', exact=True).click()
            marker = body.locator('[data-field]')
            expect(marker).to_have_text('【客户字段 unchanged】')
            expect(marker).to_have_attribute('title', 'Field: 客户字段 unchanged')
            field_id = marker.get_attribute('data-field')
            language.select_option('zh-Hant')
            expect(marker).to_have_text('【客户字段 unchanged】')
            assert marker.get_attribute('data-field') == field_id
            expect(marker).to_have_attribute('title', '字段：客户字段 unchanged')
            language.select_option('en')
            page.get_by_role("link", name="Spreadsheet", exact=True).click()
            expect(page.locator('.sheettab').first).to_be_visible()
            page.get_by_role("textbox", name="Template name").fill("表格内容 remains")
            language.select_option("zh-Hant")
            expect(page.get_by_role("textbox", name="模板名稱")).to_have_value("表格内容 remains")
            assert api_calls == [], api_calls
            page.goto(url + '?lang=en&fixture=1#retained')
            expect(language).to_have_value('en')
            assert page.url.endswith('?fixture=1#retained'), page.url
            assert page.evaluate("localStorage.getItem('synwevo.locale')") == 'en'
            language.select_option('zh-Hant')
            page.goto(url + '?lang=en-US')
            expect(language).to_have_value('zh-Hant')
            # Explicit fixture-only responses; all business endpoints remain blocked.
            resource_calls = []
            def fixture_response(route):
                resource_calls.append(route.request.url)
                assert route.request.headers.get('x-synwevo-workspace') == 'fixture-tab-A'
                headers = {'X-Request-ID': 'fixture-resource'}
                if route.request.url.endswith('/image'):
                    route.fulfill(content_type='image/png', headers=headers, body=base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+a6p8AAAAASUVORK5CYII='))
                elif route.request.url.endswith('/denied'):
                    route.fulfill(status=403, content_type='application/json', headers=headers, body=json.dumps({'error':'future_refusal','message':'Untranslated backend diagnostic','request_id':'fixture-resource'}))
                else:
                    route.fulfill(content_type='application/json', headers={**headers, 'Content-Disposition': "attachment; filename*=UTF-8''%E5%AE%A2%E6%88%B7.json"}, body='{"fixture":"用户内容"}')
            context.route('**/api/i18n-fixture/*', fixture_response)
            language.select_option('en')
            page.get_by_role('link', name='Resources', exact=True).click()
            expect(page.get_by_alt_text('Fixture image')).to_have_attribute('src', re.compile('^blob:'))
            assert page.get_by_alt_text('Fixture image').evaluate('el => el.complete && el.naturalWidth === 1')
            with page.expect_download() as download_info:
                page.get_by_role('button', name='Download fixture', exact=True).click()
            download = download_info.value
            assert download.suggested_filename == '客户.json'
            download.save_as(Path(tmp) / 'fixture.json')
            assert json.loads((Path(tmp) / 'fixture.json').read_text()) == {'fixture':'用户内容'}
            page.get_by_role('button', name='Denied fixture', exact=True).click()
            expect(page.get_by_role('status')).to_contain_text('The request could not be completed (future_refusal).')
            language.select_option('zh-Hant')
            expect(page.get_by_role('status')).to_contain_text('請求未完成（future_refusal）。')
            assert len(resource_calls) == 3, resource_calls
            assert api_calls == [], api_calls
            assert errors == [], errors
            # Browser-language negotiation when no saved preference exists.
            other = browser.new_context(locale="zh-TW")
            other.route("**/api/**", lambda route: route.abort())
            other_page = other.new_page()
            other_page.goto(url)
            expect(other_page.locator('[data-testid="fixture-controls"] select')).to_have_value("zh-Hant")
            browser.close()
            print("PASS: browser-language/default, exact navigation locale, persisted preference, lang/title, plurals, input identity/selection, unfinished value-map input, document contents/undo, sheet name, workspace-bound image/download/errors, retained error translation, no business API calls")
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
        log.close()
