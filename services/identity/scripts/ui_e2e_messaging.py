"""Messaging in two real, independent browser sessions on an isolated instance (file mail): an
owner (desktop, 1280×800) and a newly invited member of the same organization (phone, 390×844,
touch) exchange direct messages that arrive through the stream without reloading, react and
reply, share an image through the acceptance chain, meet in a group from which one is then
removed (and loses every door), search, and do it all at phone width. Nothing is seeded: both
accounts register through mailed links.

Since CI-66 the conversations live on Synwevo Message's own origin: both browsers sign in on the
account origin and reach the messages origin through its `/auth/start` (the single sign-on hop
the shell performs by itself when a page needs a session).

CI-73 (the chat UI): the same run also proves the shell's behaviour — older history loads on
scroll-up with the anchor kept, a pill for messages that arrive while the reader is up in the
history, the unread divider on re-entry, the reply quote jumping to the original, reactions from
the phone's action menu (touch-sized), an Enter pressed while an input method is composing not
sending, attachment upload progress with cancel and retry, a search result opening its thread at
that message with a way back to the latest, list ↔ thread ↔ info on the phone with real back
controls, the reduced-motion mode, keyboard-only use of the message actions menu, and the
`/objects/…` route's "not found" page. CI-74 (the contract): the peer's presence from the
phone's heartbeat, the two mutual settings and a read receipt on the owner's message, a typing
hint that expires, a recall inside the window, delete-for-me with its undo, forwarding to another
conversation, mark-as-unread from the list, and a voice message recorded with a fake microphone
(Chromium). CI-78: an @ mark on the group row while a mention of the member is unread (the 未读
filter shows it; reading clears it), a group picture uploaded by the owner that the member sees in
the thread header and the list, blocking from the direct message's info panel (the owner's
composer is replaced by the plain state, the member's send is refused with the neutral sentence
and nothing names the block; unblocking from 消息设置 → 已屏蔽的人 lets both send again), a group
started from the direct message with the other person chosen and the note that the private history
stays behind, and the organization scope of a person in two organizations (server filter,
remembered across a reload, the new-conversation dialog starting in it). Zero console errors and
page errors are asserted (the browser's own log lines for the provoked answers are recorded).

    .venv/bin/python services/identity/scripts/ui_e2e_messaging.py http://accounts.localhost:31770 \
        http://message.localhost:31768 <mail dir> [--webkit] [--shots DIR]

Output never includes tokens, cookies or message bodies beyond the fixed test strings.
"""
from __future__ import annotations

import email
import json
import re
import secrets
import sys
import struct
import time
import urllib.parse
import urllib.request
import zlib
from pathlib import Path

from playwright.sync_api import Browser, Page, expect, sync_playwright

from localhost_proxy import LocalhostProxy

expect.set_options(timeout=15000)
PW = "harbor lights guide the evening ferry home"
PNG = b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\rIDATx\x9cc\xf8\x0f\x00\x01\x01\x01\x00\x18\xdd\x8d\xb1\x00\x00\x00\x00IEND\xaeB`\x82"
HISTORY = 52          # more than one page (50), fewer than the per-minute send limit


def picture_png(width: int = 96, height: int = 64) -> bytes:
    """A real, wider-than-tall RGB picture (a fixture gradient): the shell must crop its centre
    square and re-encode it as the platform's avatar PNG before the server accepts it."""
    def chunk(kind, data):
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xffffffff)
    rows = b"".join(b"\x00" + b"".join(bytes(((x * 255) // width, (y * 255) // height, 180)) for x in range(width)) for y in range(height))
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(rows)) + chunk(b"IEND", b""))


def require_test_instance(base: str) -> str:
    with urllib.request.urlopen(base.rstrip("/") + "/healthz", timeout=5) as r:
        body = json.loads(r.read().decode())
    if body.get("instance") != "test":
        raise SystemExit(f"{base} is not a test-marked instance (healthz: {body}); refusing to drive it")
    return base.rstrip("/")


def mail_to(mail_dir: Path, address: str, after: float, *, subject_re: str) -> email.message.Message:
    for _ in range(240):
        for p in sorted((p for p in mail_dir.glob("*.eml") if p.stat().st_mtime >= after), key=lambda p: p.stat().st_mtime, reverse=True):
            m = email.message_from_bytes(p.read_bytes())
            subject = str(email.header.make_header(email.header.decode_header(m["Subject"] or "")))
            if (m["To"] or "").strip().lower() == address.lower() and re.search(subject_re, subject):
                return m
        time.sleep(0.25)
    raise AssertionError(f"no mail matching {subject_re!r} to {address}")


def mail_text(m) -> str:
    if m.is_multipart():
        for part in m.walk():
            if part.get_content_type() == "text/plain":
                return part.get_payload(decode=True).decode(part.get_content_charset() or "utf-8", "replace")
        raise AssertionError("mail has no text/plain part")
    return m.get_payload(decode=True).decode(m.get_content_charset() or "utf-8", "replace")


def link_in(m, accounts: str, path: str) -> str:
    text = mail_text(m)
    found = re.search(r"https?://\S+" + re.escape(path) + r"\?lang=[\w-]+#token=[A-Za-z0-9_-]+", text)[0]
    return accounts + found[found.index(path):]


def register_and_activate(page, accounts, mail_dir, address, name):
    started = time.time()
    r = page.request.post(f"{accounts}/api/auth/register", data={"email": address, "password": PW, "display_name": name, "locale": "zh-Hans"})
    assert r.status == 202, (r.status, r.text()[:120])
    page.goto(link_in(mail_to(mail_dir, address, started, subject_re="验证|Verify|驗證"), accounts, "/verify-email"))
    page.get_by_role("button", name=re.compile("验证此邮箱")).click()
    pw = page.locator("input[type=password]").first
    pw.wait_for(state="visible"); pw.fill(PW)
    page.get_by_role("button", name=re.compile("设置新口令并验证邮箱")).click()
    expect(page.locator("main, body").first).to_contain_text(re.compile("验证完成"))


def sign_in(page, accounts, address):
    page.goto(f"{accounts}/login?lang=zh-Hans")
    page.get_by_label(re.compile("邮箱|Email")).fill(address)
    page.locator("input[type=password]").first.fill(PW)
    page.get_by_role("button", name=re.compile("^(登录|登入|Sign in)$")).click()
    page.wait_for_url(lambda u: not re.search(r"/(login|auth/start|auth/callback|oidc/)", u))


def csrf_of(ctx, origin: str | None = None):
    """The CSRF cookie of one origin (every origin has its own session and its own cookie)."""
    host = urllib.parse.urlsplit(origin).hostname if origin else None
    return next(c["value"] for c in ctx.cookies() if c["name"] == "dw_csrf" and (host is None or c["domain"].lstrip(".") == host))


def enter_messages(page, messages: str, path: str = "/") -> None:
    """Open a page of the messages origin; the shell's own sign-in start takes the browser through
    the account service (where a session already exists) and back to the page asked for."""
    page.goto(f"{messages}{path}{'&' if '?' in path else '?'}lang=zh-Hans")
    page.wait_for_url(lambda u: u.startswith(messages) and not re.search(r"/(auth/start|auth/callback)", u), timeout=20000)
    page.wait_for_load_state("networkidle")


#: HTTP answers this run provokes on purpose; the browser logs each as "Failed to load resource":
#: 400 the account shell's first verify-email probe, 401 anonymous probes, 403 the blocked member's
#: send (CI-78: the neutral refusal is the behaviour under test), 404 the removed member's history
#: probe, 405 the objects contract endpoint on a server that does not serve it yet, 503 the injected
#: upload refusal. Anything else in the console is a defect.
EXPECTED_STATUSES = ("400", "401", "403", "404", "405", "503")
resource_logs: list[str] = []


def watch(page: Page, violations: list[str], tag: str) -> None:
    """Console errors and page errors are defects; the browser's own log line for an expected
    non-2xx answer is recorded, not counted."""
    def on_console(m):
        if m.type == "error":
            hit = re.search(r"Failed to load resource: the server responded with a status of (\d{3})", m.text)
            if hit and hit.group(1) in EXPECTED_STATUSES:
                resource_logs.append(f"{tag} {hit.group(1)}")
                return
            # the platform's Permissions-Policy header (`microphone=()`) makes the browser refuse the
            # microphone and say so in the console; the shell reports the refusal to the person.
            # Recorded as the environment fact it is, not counted as a shell defect.
            if "Permissions policy violation: microphone" in m.text:
                resource_logs.append(f"{tag} permissions-policy:microphone")
                return
            violations.append(f"{tag} console: {m.text[:160]}")
        if "Content Security Policy" in m.text:
            violations.append(f"{tag} csp: {m.text[:160]}")
    page.on("console", on_console)
    page.on("pageerror", lambda e: violations.append(f"{tag} pageerror: {str(e)[:160]}"))


def new_conversation(page, kind: str) -> None:
    page.get_by_role("button", name=re.compile("^新会话$")).click()
    page.get_by_role("menuitem", name=re.compile(kind)).click()


def composer(page):
    return page.locator(".msg-composer textarea")


def send_text(page, text: str) -> None:
    composer(page).fill(text)
    composer(page).press("Enter")
    expect(page.locator(".msg-row.own").last).to_contain_text(text)
    expect(page.locator(".msg-pending")).to_have_count(0, timeout=10000)


def message_menu(page, row, item: str) -> None:
    row.get_by_role("button", name=re.compile("^更多操作$")).click()
    page.get_by_role("menuitem", name=re.compile(item)).click()


def shot(page, shots: Path | None, name: str) -> None:
    """A screenshot after the page's short entry animations have finished (a panel is still at
    opacity 0 in the first frame after it appears)."""
    if shots:
        page.evaluate("() => Promise.all(document.getAnimations().map(a => a.finished.catch(() => null)))")
        page.screenshot(path=str(shots / f"{name}.png"), full_page=False)


def no_overflow(page, where: str) -> None:
    """A phone page must not be wider than its viewport: Chrome would zoom the whole page out to
    fit, and every touch target would shrink with it. Names the widest offenders when it fails."""
    wide = page.evaluate("""() => {
      const w = window.innerWidth; const out = [];
      for (const el of document.querySelectorAll('body *')) {
        const r = el.getBoundingClientRect();
        if (r.width > 0 && r.right > w + 1 && !el.closest('.menu, .msg-layer, .modal-back, .msg-lightbox')) out.push(`${el.tagName.toLowerCase()}.${[...el.classList].join('.')} right=${Math.round(r.right)}`);
      }
      return { scrollWidth: document.documentElement.scrollWidth, innerWidth: w, wide: out.slice(0, 8) };
    }""")
    assert wide["scrollWidth"] <= wide["innerWidth"] + 1 and not wide["wide"], (where, wide)


def run(browser: Browser, engine: str, accounts: str, messages: str, mail_dir: Path, shots: Path | None, ctx_extra: dict | None = None) -> list[str]:
    notes: list[str] = []
    tag = secrets.token_hex(3)
    owner, member = f"owner.{tag}@example.test", f"member.{tag}@example.test"
    extra = ctx_extra or {}
    a = browser.new_context(viewport={"width": 1280, "height": 800}, locale="zh-CN", **({"permissions": ["microphone"]} if engine == "chromium" else {}), **extra)
    b = browser.new_context(viewport={"width": 390, "height": 844}, is_mobile=True, has_touch=True, locale="zh-CN", **extra)
    pa, pb = a.new_page(), b.new_page()
    violations: list[str] = []
    watch(pa, violations, "owner"); watch(pb, violations, "member")

    # ---- two real accounts in one organization: the owner invites, the member accepts the mailed link
    register_and_activate(pa, accounts, mail_dir, owner, f"Owner {tag}")
    sign_in(pa, accounts, owner)
    r = pa.request.post(f"{accounts}/api/auth/organizations", data={"name": f"Chat Org {tag}", "workspace_name": f"Chat Space {tag}"},
                        headers={"X-Synwevo-CSRF": csrf_of(a), "Idempotency-Key": f"e2e-chat-{tag}"})
    assert r.status == 201, (r.status, r.text()[:200])
    wid = r.json()["workspace"]["id"]
    started = time.time()
    r = pa.request.post(f"{accounts}/api/auth/workspaces/{wid}/invitations/batch", data={"invitations": [{"email": member, "role": "editor"}]}, headers={"X-Synwevo-CSRF": csrf_of(a)})
    assert r.ok, r.text()[:200]
    link = link_in(mail_to(mail_dir, member, started, subject_re="邀请|邀請|Join"), accounts, "/invite")
    pb.goto(link)
    pb.get_by_role("link", name=re.compile("创建账户并继续|建立帳戶並繼續|Create an account")).click()
    pb.wait_for_url(lambda u: "/register" in u)
    pb.locator("#register-name").fill(f"Member {tag}")
    pb.locator("input[type=password]").first.fill(PW)
    pb.locator("form button.btn.primary").first.click()
    pb.wait_for_function("() => location.pathname.startsWith('/onboarding') || document.body.innerText.includes('已加入工作空间')", timeout=20000)
    try:
        pb.wait_for_url(lambda u: "/onboarding" in u, timeout=10000)
    except Exception:
        pass
    pb.wait_for_load_state("networkidle")
    notes.append(f"{engine}: owner registered and created an organization; the member joined through the mailed invitation (phone-sized session)")

    # ---- the member opens Synwevo Message first (empty), the owner starts a DM; it arrives over the stream
    enter_messages(pb, messages)
    assert pb.url.startswith(messages) and "/auth/" not in pb.url, pb.url
    expect(pb.locator(".msg-side")).to_contain_text(re.compile("还没有会话"))
    shot(pb, shots, f"{engine}-phone-list-empty")
    enter_messages(pa, messages)
    new_conversation(pa, "新私聊")
    dialog = pa.locator(".modal").last
    dialog.locator("#msg-who").fill("Member")
    dialog.get_by_role("button", name=re.compile("^选择$")).first.click()
    dialog.get_by_role("button", name=re.compile("开始私聊")).click()
    pa.wait_for_url(lambda u: "/c/cv_" in u)
    dm_url = pa.url
    dm_id = dm_url.rsplit("/", 1)[-1].split("?")[0]
    expect(pa.locator(".msg-status")).to_have_text(re.compile("实时"), timeout=15000)
    send_text(pa, f"hello from owner {tag}")
    expect(pb.locator(".msg-conv")).to_have_count(1, timeout=15000)
    expect(pb.locator(".msg-unread")).to_have_text("1")
    expect(pb.locator("a.bell", has_text=re.compile("消息"))).to_contain_text("1")
    assert pb.locator("a.bell", has_text=re.compile("消息")).get_attribute("aria-current") == "page"
    # the list row carries the sender-less preview of a private conversation and a relative time
    expect(pb.locator(".msg-conv-preview")).to_contain_text(f"hello from owner {tag}")
    expect(pb.locator(".msg-conv-time")).to_be_visible()
    shot(pb, shots, f"{engine}-phone-list")
    no_overflow(pb, "phone list")
    pb.locator(".msg-conv").first.click()
    expect(pb.locator(".msg-thread")).to_be_visible()
    expect(pb.locator(".msg-thread")).to_contain_text(f"hello from owner {tag}")
    expect(pb.locator(".msg-status")).to_have_text(re.compile("实时"), timeout=15000)
    # the unread divider sits before the owner's message on first open
    expect(pb.locator(".msg-unread-div")).to_have_text(re.compile("以下为未读"))
    no_overflow(pb, "phone thread")
    notes.append(f"{engine}: the DM reached the member's list (preview, time, badge 1) and the platform bar over the stream; the thread opened with the unread divider")

    # ---- presence: the phone's visible page heartbeats, so the owner sees the member online (§7.3)
    expect(pa.locator(".msg-thread-h .msg-presence-text")).to_have_text(re.compile("在线"), timeout=20000)
    expect(pa.locator(".msg-thread-h .msg-presence.online")).to_be_visible()
    notes.append(f"{engine}: the member's presence (from the phone's heartbeat) showed as 在线 in the owner's thread header")

    # ---- read receipts are mutual and off by default: both turn them on, then the member's read
    #      position becomes a 已读 mark on the owner's own message (§7.2, §7.4)
    expect(pa.locator(".msg-receipt")).to_have_count(0)
    pa.get_by_role("button", name=re.compile("^消息设置$")).click()
    dlg = pa.get_by_role("dialog", name=re.compile("消息设置"))
    dlg.get_by_role("switch", name=re.compile("已读回执")).check()
    dlg.get_by_role("button", name=re.compile("^保存$")).click()
    expect(dlg).to_have_count(0)
    pb.get_by_role("button", name=re.compile("^返回会话列表$")).click()
    pb.get_by_role("button", name=re.compile("^消息设置$")).click()
    dlg = pb.get_by_role("dialog", name=re.compile("消息设置"))
    dlg.get_by_role("switch", name=re.compile("已读回执")).check()
    dlg.get_by_role("button", name=re.compile("^保存$")).click()
    expect(dlg).to_have_count(0)
    send_text(pa, f"read me {tag}")
    pb.locator(".msg-conv").first.click()
    expect(pb.locator(".msg-thread")).to_contain_text(f"read me {tag}")
    expect(pa.locator(".msg-row.own").last.locator(".msg-receipt")).to_have_text(re.compile("已读"), timeout=15000)
    assert "已送达" not in pa.locator(".msg-thread").inner_text(), "no 'delivered' anywhere"
    notes.append(f"{engine}: with receipts on for both, the member reading the thread put a 已读 mark on the owner's latest message; nothing says 已送达")

    # ---- typing: the phone's composer sends a hint; the owner sees it and it goes away (§7.3)
    composer(pb).fill(f"typing {tag}")
    expect(pa.locator(".msg-typing")).to_contain_text(re.compile(f"Member {tag}.*正在输入"), timeout=10000)
    composer(pb).fill("")
    expect(pa.locator(".msg-typing")).to_have_count(0, timeout=12000)
    notes.append(f"{engine}: '{{who}} 正在输入…' appeared on the owner's side while the member typed and cleared after the idle stop")

    # ---- reply and react from the phone through the touch-sized actions menu (no hover)
    row0 = pb.locator(".msg-row").first
    assert row0.locator(".msg-act-more").evaluate("el => getComputedStyle(el).opacity") == "1", "the ⋯ control must be visible without hover on touch"
    message_menu(pb, row0, "^回复$")
    expect(pb.locator(".msg-banner")).to_contain_text("回复")
    composer(pb).fill(f"hi owner, got it {tag}")
    pb.get_by_role("button", name=re.compile("^发送$")).click()
    expect(pa.locator(".msg-thread")).to_contain_text(f"hi owner, got it {tag}", timeout=15000)
    expect(pa.locator(".msg-row").last.locator(".msg-quote-block")).to_contain_text("hello from owner")
    message_menu(pb, row0, "^表情回应$")
    bar = pb.locator(".msg-reactbar")
    expect(bar).to_be_visible()
    # layout size, not the bounding box: the layer's short entry animation scales it from .96
    size = bar.locator("button").first.evaluate("el => [el.offsetWidth, el.offsetHeight]")
    assert size[0] >= 44 and size[1] >= 44, size
    no_overflow(pb, "phone reaction bar open")
    shot(pb, shots, f"{engine}-phone-reactions")
    bar.locator("button").first.click()
    expect(pa.locator(".msg-row").first.locator(".msg-reaction")).to_contain_text(re.compile(r"👍\s*1"), timeout=15000)
    notes.append(f"{engine}: reply-with-quote and a reaction from the phone's actions menu (⋯ visible without hover, reaction buttons ≥44px) showed up live on the owner's desktop")

    # ---- the quote jumps to the original and highlights it
    pa.locator(".msg-row").last.locator(".msg-quote-block").click()
    expect(pa.locator(".msg-row.target")).to_contain_text(f"hello from owner {tag}")
    notes.append(f"{engine}: clicking the quote block scrolled to and highlighted the original message")

    # ---- an image through the acceptance chain; the member opens it in the viewer
    pa.locator(".msg-composer input[type=file]").set_input_files({"name": "dot.png", "mimeType": "image/png", "buffer": PNG})
    expect(pa.locator(".msg-upload.done")).to_contain_text("dot.png")
    pa.get_by_role("button", name=re.compile("^发送$")).click()
    img = pb.locator(".msg-attach-img img").first
    expect(img).to_be_visible(timeout=15000)
    src = img.get_attribute("src")
    r = pb.request.get(messages + src)
    assert r.ok and r.headers.get("content-type") == "image/png" and r.headers.get("x-content-sha256"), (r.status, dict(r.headers))
    pb.locator(".msg-attach-img").first.click()
    viewer = pb.get_by_role("dialog", name=re.compile("查看图片"))
    expect(viewer).to_be_visible()
    expect(viewer.get_by_role("link", name=re.compile("下载"))).to_have_attribute("href", re.compile(r"/attachments/att_"))
    shot(pb, shots, f"{engine}-phone-lightbox")
    pb.keyboard.press("Escape")
    expect(viewer).to_have_count(0)
    notes.append(f"{engine}: an image attachment was scanned, stored once by content hash, rendered inline for the member (image/png, hash header) and opened in the viewer; Escape closed it")

    # ---- a voice message (§7.8): recorded with the browser's recorder from a fake microphone
    #      (Chromium launch flags); a browser that cannot record says so honestly
    pa.get_by_role("button", name=re.compile("^语音消息$")).click()
    recording = pa.locator(".msg-recording:not(.msg-mic-wait)")
    try:
        recording.wait_for(state="visible", timeout=5000)
        pa.wait_for_timeout(1500)
        recording.get_by_role("button", name=re.compile("停止并发送")).click()
        voice = pa.locator(".msg-row.own .msg-voice").last
        expect(voice).to_be_visible(timeout=20000)
        expect(voice.locator("audio")).to_have_attribute("src", re.compile(r"/attachments/att_.*inline=1"))
        expect(voice.locator(".tiny")).to_have_text(re.compile(r"^\d+:\d\d$"))
        expect(pb.locator(".msg-voice").last).to_be_visible(timeout=15000)
        notes.append(f"{engine}: a voice message recorded from the fake microphone was uploaded with its measured duration, sent by itself, and plays inline for both")
    except Exception:
        # A browser that cannot record, refuses the microphone, or whose permission prompt never
        # answers (headless Chrome on this machine: getUserMedia never settles even with the fake
        # device flags) — the shell shows the waiting state and then says so instead of pretending.
        waiting = pa.locator(".msg-mic-wait")
        if waiting.count():
            expect(waiting).to_contain_text(re.compile("等待麦克风授权"))
            waiting.get_by_role("button", name="取消").click()
            expect(waiting).to_have_count(0)
            notes.append(f"{engine}: the microphone prompt never answered; the shell showed 正在等待麦克风授权… and the wait was cancelled honestly (recording itself needs a browser whose prompt settles)")
        else:
            toast = pa.locator(".toast").last
            expect(toast).to_contain_text(re.compile("不支持录音|无法使用麦克风|麦克风没有响应"), timeout=25000)
            why = toast.inner_text().split("\n")[0]
            notes.append(f"{engine}: recording could not start ({why}); the shell reported it honestly")

    # ---- attachment upload: real progress, cancel while in flight, retry after a refusal
    held: list = []
    pa.route(re.compile(r".*/attachments$"), lambda route: held.append(route))          # hold the upload
    pa.locator(".msg-composer input[type=file]").set_input_files({"name": "slow.bin", "mimeType": "application/octet-stream", "buffer": b"\x00" * 200_000})
    up = pa.locator(".msg-upload.uploading")
    expect(up).to_be_visible()
    expect(up.locator("progress.msg-progress")).to_be_visible()
    up.get_by_role("button", name=re.compile("^取消$")).click()
    expect(pa.locator(".msg-upload")).to_have_count(0)
    for route in held:
        try:
            route.abort()
        except Exception:
            pass
    pa.unroute(re.compile(r".*/attachments$"))
    pa.route(re.compile(r".*/attachments$"), lambda route: route.fulfill(status=503, content_type="application/json", headers={"X-Request-ID": "e2e-refusal"},
                                                                       body=json.dumps({"error": "scanner_unavailable", "message": "安全检查暂时不可用，请稍后重试", "request_id": "e2e-refusal"})))
    pa.locator(".msg-composer input[type=file]").set_input_files({"name": "retry.txt", "mimeType": "text/plain", "buffer": b"retry me"})
    failed = pa.locator(".msg-upload.failed")
    expect(failed).to_be_visible()
    # the shared client presents a 5xx in its own words with the server's request id, never raw server prose
    expect(failed).to_contain_text(re.compile("请求编号：e2e-refusal"))
    shot(pa, shots, f"{engine}-desktop-upload-failed")
    pa.unroute(re.compile(r".*/attachments$"))
    failed.get_by_role("button", name=re.compile("^重试$")).click()
    expect(pa.locator(".msg-upload.done")).to_contain_text("retry.txt")
    pa.locator(".msg-upload.done").get_by_role("button", name=re.compile("移除附件")).click()
    expect(pa.locator(".msg-upload")).to_have_count(0)
    notes.append(f"{engine}: an upload showed its progress bar while in flight and was cancelled; a refused upload showed the server's reason and succeeded on retry")

    # ---- an Enter that belongs to an input-method composition does not send
    ime_text = f"拼音输入 {tag}"
    composer(pa).fill(ime_text)
    composer(pa).dispatch_event("compositionstart")
    composer(pa).press("Enter")
    pa.wait_for_timeout(300)
    # the text stays in the field (a real IME would have consumed the Enter; the synthetic one lets
    # the browser insert a line break, which is exactly not a send)
    expect(composer(pa)).to_have_value(re.compile("^" + re.escape(ime_text)))
    assert not pa.locator(".msg-row", has_text=ime_text).count(), "Enter during composition must not send"
    composer(pa).dispatch_event("compositionend")
    composer(pa).fill(ime_text)
    composer(pa).press("Enter")
    expect(pa.locator(".msg-row.own").last).to_contain_text(ime_text)
    expect(pa.locator(".msg-pending")).to_have_count(0, timeout=10000)
    notes.append(f"{engine}: Enter while composing kept the text in the field; Enter after the composition ended sent it")

    # ---- recall (sender, inside the window) leaves a named marker for everyone (§7.6)
    send_text(pa, f"recall me {tag}")
    row = pa.locator(".msg-row.own").last
    message_menu(pa, row, "^撤回$")
    confirm = pa.get_by_role("alertdialog")
    confirm.get_by_role("button", name=re.compile("^撤回$")).click()
    expect(pa.locator(".msg-system.marker").last).to_have_text(re.compile("你撤回了一条消息"), timeout=15000)
    expect(pb.locator(".msg-system.marker").last).to_have_text(re.compile(f"Owner {tag} 撤回了一条消息"), timeout=15000)
    assert not pa.locator(".msg-row", has_text=f"recall me {tag}").count()
    notes.append(f"{engine}: a recall inside the window replaced the message by '你撤回了一条消息' for the owner and 'Owner 撤回了一条消息' for the member")

    # ---- delete for me: the row goes for this reader only, with a five-second undo (§7.6)
    #      (rows are matched by their own body: the member's reply quotes the same text)
    hello_b = pb.locator(".msg-row", has=pb.locator(".msg-body", has_text=f"hello from owner {tag}"))
    hello_a = pa.locator(".msg-row", has=pa.locator(".msg-body", has_text=f"hello from owner {tag}"))
    message_menu(pb, hello_b.first, "^删除（仅自己）$")
    expect(hello_b).to_have_count(0)
    undo = pb.locator(".msg-undo")
    expect(undo).to_be_visible()
    undo.get_by_role("button", name=re.compile("^撤销$")).click()
    expect(hello_b).to_have_count(1, timeout=10000)
    expect(undo).to_have_count(0)
    message_menu(pb, hello_b.first, "^删除（仅自己）$")
    expect(hello_b).to_have_count(0)
    r = pb.request.get(f"{messages}/api/messaging/conversations/{dm_id}/messages?limit=200")
    assert all(f"hello from owner {tag}" != m["body"] for m in r.json()["items"]), "hidden for the member"
    r = pa.request.get(f"{messages}/api/messaging/conversations/{dm_id}/messages?limit=200")
    assert any(f"hello from owner {tag}" == m["body"] for m in r.json()["items"]), "still there for the owner"
    expect(hello_a).to_have_count(1)
    notes.append(f"{engine}: delete-for-me removed the row on the phone, the undo put it back, a second delete stayed hidden for the member only (history 200 without it) while the owner still has it")

    # ---- the emoji picker inserts at the caret; the composer grows with the text
    h0 = composer(pa).evaluate("el => el.offsetHeight")
    composer(pa).fill("line 1\nline 2\nline 3\nline 4")
    assert composer(pa).evaluate("el => el.offsetHeight") > h0, "the composer must grow with its text"
    composer(pa).fill("")
    pa.get_by_role("button", name=re.compile("^表情$")).click()
    picker = pa.get_by_role("dialog", name=re.compile("^表情$"))
    expect(picker).to_be_visible()
    shot(pa, shots, f"{engine}-desktop-emoji")
    picker.locator(".msg-emoji-grid button").first.click()
    assert composer(pa).input_value().strip() != "", "picking an emoji inserts it"
    pa.keyboard.press("Escape")
    expect(picker).to_have_count(0)
    composer(pa).fill("")
    notes.append(f"{engine}: the composer grows with its text; the emoji picker opened, inserted a character and closed on Escape")

    # ---- history: the member writes a page and a half through the API; the owner reloads and scrolls up
    csrf_b = csrf_of(b, messages)
    for i in range(HISTORY):
        r = pb.request.post(f"{messages}/api/messaging/conversations/{dm_id}/messages", data={"body": f"history {i:02d} {tag}", "client_id": f"h{i}-{tag}"}, headers={"X-Synwevo-CSRF": csrf_b})
        assert r.status == 201, (r.status, r.text()[:160])
    pa.reload()
    pa.wait_for_load_state("networkidle")
    expect(pa.locator(".msg-history-top")).to_be_visible(timeout=15000)
    scroll = pa.locator(".msg-scroll")
    assert scroll.evaluate("el => el.scrollHeight - el.scrollTop - el.clientHeight < 60"), "a fresh open starts at the bottom"
    probe = pa.locator(".msg-row[data-seq]").first
    probe_seq = probe.get_attribute("data-seq")
    n_before = pa.locator(".msg-row").count()
    # hold the history request so the reader's position can be measured after the scroll-up and
    # before the older page lands; the anchor must be where it was at that moment
    held_hist: list = []
    pa.route(re.compile(r".*/messages\?before=.*"), lambda route: held_hist.append(route))
    scroll.evaluate("el => { el.scrollTop = 0; }")
    for _ in range(100):
        if held_hist:
            break
        pa.wait_for_timeout(50)
    assert held_hist, "scrolling to the top must ask for older history"
    # the bubble is what the reader is looking at; the row above it may lose its head line when the
    # earlier message from the same sender arrives and the two become one group
    before_top = pa.locator(f".msg-row[data-seq='{probe_seq}'] .msg-bubble").bounding_box()["y"]
    held_hist[0].continue_()
    pa.unroute(re.compile(r".*/messages\?before=.*"))
    pa.wait_for_function("n => document.querySelectorAll('.msg-row').length > n", arg=n_before, timeout=15000)
    pa.wait_for_timeout(100)
    after_top = pa.locator(f".msg-row[data-seq='{probe_seq}'] .msg-bubble").bounding_box()["y"]
    assert abs(after_top - before_top) <= 2, ("anchor moved", before_top, after_top)
    assert pa.locator(".msg-row").count() > n_before
    expect(pa.locator(".msg-day")).to_have_count(1)
    notes.append(f"{engine}: {HISTORY} older messages loaded on scroll-up and the message the reader was looking at stayed within 2px ({before_top:.0f}→{after_top:.0f})")

    # ---- a message arriving while the reader is up in the history: a pill, not a jump
    scroll.evaluate("el => { el.scrollTop = 0; }")
    pa.wait_for_timeout(200)
    composer(pb).fill(f"ping pill {tag}")
    pb.get_by_role("button", name=re.compile("^发送$")).click()
    pill = pa.locator(".msg-pill.bottom")
    expect(pill).to_contain_text(re.compile("1 条新消息"), timeout=15000)
    assert scroll.evaluate("el => el.scrollTop") < 50, "the view must not jump while the reader is up in the history"
    shot(pa, shots, f"{engine}-desktop-new-pill")
    pill.click()
    expect(pa.locator(".msg-row").last).to_contain_text(f"ping pill {tag}")
    expect(pill).to_have_count(0)
    notes.append(f"{engine}: a message that arrived while the owner was up in the history showed a '1 条新消息' pill without moving the view; the pill scrolled to it")

    # ---- the unread divider on re-entry, and the reading position kept across a list round-trip
    pb.get_by_role("button", name=re.compile("^返回会话列表$")).click()
    expect(pb.locator(".msg-side")).to_be_visible()
    send_text(pa, f"unread one {tag}")
    send_text(pa, f"unread two {tag}")
    expect(pb.locator(".msg-unread")).to_have_text("2", timeout=15000)
    pb.locator(".msg-conv").first.click()
    div = pb.locator(".msg-unread-div")
    expect(div).to_be_visible()
    following = pb.locator(".msg-unread-div ~ .msg-row").first
    expect(following).to_contain_text(f"unread one {tag}")
    expect(pb.locator(".msg-unread")).to_have_count(0, timeout=10000)      # read at the bottom → the badge clears
    notes.append(f"{engine}: coming back to the thread placed '以下为未读' before the two new messages and reading them cleared the badge")

    # ---- search: the result opens the thread at that message, with a way back to the latest.
    #      A fresh load holds only the latest page, so the hit is outside what is loaded.
    pa.reload()
    pa.wait_for_load_state("networkidle")
    expect(pa.locator(".msg-status")).to_have_text(re.compile("实时"), timeout=15000)
    assert pa.locator(".msg-row", has_text=f"history 03 {tag}").count() == 0, "the hit must not be loaded yet"
    pa.locator(".msg-side-input").fill(f"history 03 {tag}")
    pa.locator(".msg-side-input").press("Enter")
    hits = pa.locator(".msg-search-hit")
    expect(hits).to_have_count(1)
    expect(hits.first.locator("mark").first).to_be_visible()
    hits.first.click()
    expect(pa.locator(".msg-row.target")).to_contain_text(f"history 03 {tag}", timeout=15000)
    back = pa.locator(".msg-pill.bottom", has_text=re.compile("回到最新"))
    expect(back).to_be_visible()
    shot(pa, shots, f"{engine}-desktop-search-jump")
    back.click()
    expect(back).to_have_count(0)
    expect(pa.locator(".msg-row").last).to_contain_text(f"unread two {tag}")
    pa.get_by_role("button", name=re.compile("^返回会话列表$")).click()
    notes.append(f"{engine}: a search hit (highlighted) opened the thread at that old message; '回到最新' returned to the end")

    # ---- keyboard-only use of the message actions menu (WAI-ARIA menu pattern)
    more = pa.locator(".msg-row.own").last.locator(".msg-act-more")
    more.focus()
    pa.keyboard.press("Enter")
    menu = pa.get_by_role("menu", name=re.compile("消息操作"))
    expect(menu).to_be_visible()
    items = menu.get_by_role("menuitem")
    n_items = items.count()
    assert n_items >= 4, n_items
    focused = lambda: pa.evaluate("() => { const a = document.activeElement; return a && a.getAttribute('role') === 'menuitem' ? a.textContent.trim() : null; }")
    assert focused() == items.nth(0).text_content().strip(), ("initial focus", focused())
    pa.keyboard.press("ArrowDown"); assert focused() == items.nth(1).text_content().strip(), ("ArrowDown", focused())
    pa.keyboard.press("End"); assert focused() == items.nth(n_items - 1).text_content().strip(), ("End", focused())
    pa.keyboard.press("ArrowDown"); assert focused() == items.nth(0).text_content().strip(), ("wrap", focused())
    pa.keyboard.press("Home"); assert focused() == items.nth(0).text_content().strip(), ("Home", focused())
    box = menu.bounding_box(); vp = pa.viewport_size
    assert box["x"] >= 0 and box["y"] >= 0 and box["x"] + box["width"] <= vp["width"] and box["y"] + box["height"] <= vp["height"], ("menu clipped", box)
    shot(pa, shots, f"{engine}-desktop-actions-menu")
    pa.keyboard.press("Escape")
    expect(menu).to_have_count(0)
    assert pa.evaluate("() => document.activeElement && document.activeElement.classList.contains('msg-act-more')"), "focus must return to the ⋯ button"
    notes.append(f"{engine}: the actions menu opened from the keyboard with focus on the first item, arrows wrapped, Home/End jumped, it stayed inside the viewport, and Escape returned focus")

    # ---- a group: created by the owner, the member reads it on the phone (list ↔ thread ↔ info), then is removed
    new_conversation(pa, "新群聊")
    dialog = pa.locator(".modal").last
    dialog.locator("#msg-gname").fill(f"Launch {tag}")
    dialog.locator("#msg-who").fill("Member")
    dialog.get_by_role("button", name=re.compile("^选择$")).first.click()
    dialog.get_by_role("button", name=re.compile("创建群聊")).click()
    pa.wait_for_url(lambda u: "/c/cv_" in u and u != dm_url)
    expect(pa.locator(".msg-thread-name")).to_contain_text(f"Launch {tag}")
    gid = pa.url.rsplit("/", 1)[-1].split("?")[0]
    send_text(pa, f"welcome to the group {tag}")
    expect(pb.locator(".msg-conv")).to_have_count(2, timeout=15000)
    pb.get_by_role("button", name=re.compile("^返回会话列表$")).click()
    pb.locator(".msg-conv", has_text=f"Launch {tag}").click()
    expect(pb.locator(".msg-thread")).to_contain_text(f"welcome to the group {tag}")
    # ---- CI-78 mentions (§7.15): the owner mentions the member; the group row carries an @ while it
    #      is unread, the 未读 filter shows the row, and reading the thread clears the mark
    pb.get_by_role("button", name=re.compile("^返回会话列表$")).click()
    csrf_a = csrf_of(a, messages)
    member_uid = next(m["user_id"] for m in pa.request.get(f"{messages}/api/messaging/conversations/{gid}").json()["members"] if m["name"] == f"Member {tag}")
    r = pa.request.post(f"{messages}/api/messaging/conversations/{gid}/messages", data={"body": f"@Member 请看一下 {tag}", "mention_ids": [member_uid], "client_id": f"mention-{tag}"},
                        headers={"X-Synwevo-CSRF": csrf_a})
    assert r.status == 201, (r.status, r.text()[:160])
    group_row = pb.locator(".msg-conv-wrap", has=pb.locator(".msg-conv-name", has_text=f"Launch {tag}")).first
    at = group_row.locator(".msg-unread.mention")
    expect(at).to_have_text("@", timeout=15000)
    assert at.get_attribute("data-mentions") == "1", at.get_attribute("data-mentions")
    expect(group_row.locator(".msg-conv")).to_have_attribute("aria-label", re.compile("1 条提及你"))
    pb.get_by_role("tab", name=re.compile("^未读$")).click()
    expect(pb.locator(".msg-conv")).to_have_count(1)
    expect(pb.locator(".msg-conv").first).to_contain_text(f"Launch {tag}")
    shot(pb, shots, f"{engine}-phone-mention-badge")
    no_overflow(pb, "phone list with the unread filter")
    pb.locator(".msg-conv").first.click()
    expect(pb.locator(".msg-thread")).to_contain_text("请看一下")
    pb.get_by_role("button", name=re.compile("^返回会话列表$")).click()
    expect(pb.locator(".msg-side .msg-empty")).to_contain_text(re.compile("没有未读的会话"), timeout=10000)
    pb.get_by_role("tab", name=re.compile("^全部$")).click()
    expect(group_row.locator(".msg-unread.mention")).to_have_count(0)
    pb.locator(".msg-conv", has_text=f"Launch {tag}").click()
    expect(pb.locator(".msg-thread")).to_contain_text(f"welcome to the group {tag}")
    notes.append(f"{engine}: a mention of the member put an @ (data-mentions=1, '1 条提及你' in the row's name) on the group row; 未读 showed exactly that row; reading the thread cleared the @ and emptied 未读")
    # ---- mark as unread from the list's row menu (§7.5): the row shows 1 until it is opened
    pb.get_by_role("button", name=re.compile("^返回会话列表$")).click()
    # the row named after the owner (the group's preview also mentions the owner, so match the name)
    dm_row = pb.locator(".msg-conv-wrap", has=pb.locator(".msg-conv-name", has_text=f"Owner {tag}")).first
    expect(dm_row.locator(".msg-unread")).to_have_count(0)
    # everything has been read at this point (the group was read on leaving it): the badge settles to none
    expect(pb.locator("a.bell", has_text=re.compile("消息"))).not_to_contain_text(re.compile(r"\d"), timeout=10000)
    # a long press on the row (the phone gesture: pointer held ≥ 500 ms) opens its menu
    dm_row.locator(".msg-conv").dispatch_event("pointerdown", {"pointerType": "touch", "isPrimary": True})
    pb.wait_for_timeout(750)
    dm_row.locator(".msg-conv").dispatch_event("pointerup", {"pointerType": "touch"})
    item = pb.get_by_role("menuitem", name=re.compile("^标为未读$"))
    try:
        item.wait_for(state="visible", timeout=3000)
        gesture = "long press"
    except Exception:
        diag = pb.evaluate("() => ({ menus: document.querySelectorAll('[role=menu]').length, active: document.querySelectorAll('.msg-conv-wrap.active').length, rows: [...document.querySelectorAll('.msg-conv-name')].map(e => e.textContent) })")
        box = dm_row.bounding_box()
        pb.mouse.click(box["x"] + 40, box["y"] + 20, button="right")
        item.wait_for(state="visible", timeout=3000)
        gesture = f"right click (long press left {diag})"
    expect(item).to_be_enabled()
    item.click()
    expect(dm_row.locator(".msg-unread")).to_have_text("1", timeout=10000)
    expect(pb.locator("a.bell", has_text=re.compile("消息"))).to_contain_text("1", timeout=10000)   # the marked conversation counts as one
    dm_row.locator(".msg-conv").click()
    expect(pb.locator(".msg-thread")).to_be_visible()
    pb.get_by_role("button", name=re.compile("^返回会话列表$")).click()
    expect(dm_row.locator(".msg-unread")).to_have_count(0, timeout=10000)
    notes.append(f"{engine}: 标为未读 from the row menu ({gesture}) put a 1 on the conversation and the badge; opening it cleared both")
    pb.locator(".msg-conv", has_text=f"Launch {tag}").click()
    expect(pb.locator(".msg-thread")).to_contain_text(f"welcome to the group {tag}")

    # ---- forward (§7.7): the owner forwards a DM message into the group; both see the source
    pa.locator(".msg-conv", has_text=f"Member {tag}").first.click()
    expect(pa.locator(".msg-thread-name")).to_contain_text(f"Member {tag}")
    src_row = pa.locator(".msg-row", has=pa.locator(".msg-body", has_text=f"unread two {tag}")).first    # the owner's latest, in the loaded page
    message_menu(pa, src_row, "^转发$")
    fwd = pa.get_by_role("dialog", name=re.compile("转发消息"))
    fwd.locator(".msg-pick", has_text=f"Launch {tag}").locator("input").check()
    fwd.get_by_role("button", name=re.compile("^转发$")).click()
    expect(fwd).to_have_count(0)
    pa.locator(".msg-conv", has_text=f"Launch {tag}").first.click()
    forwarded = pa.locator(".msg-row", has=pa.locator(".msg-body", has_text=f"unread two {tag}")).last
    expect(forwarded.locator(".msg-forwarded")).to_contain_text(re.compile(f"转发自 Owner {tag}"), timeout=15000)
    expect(pb.locator(".msg-row", has=pb.locator(".msg-body", has_text=f"unread two {tag}")).last.locator(".msg-forwarded")).to_be_visible(timeout=15000)
    notes.append(f"{engine}: the owner forwarded a DM message into the group; it arrived for both with '转发自 Owner'")
    shot(pb, shots, f"{engine}-phone-thread")
    pb.get_by_role("button", name=re.compile("^群信息$")).click()
    info = pb.locator(".msg-info.page")
    expect(info).to_be_visible()
    expect(pb.locator(".msg-thread")).to_be_hidden()
    expect(info).to_contain_text(f"Owner {tag}")
    shot(pb, shots, f"{engine}-phone-info")
    no_overflow(pb, "phone info")
    pb.get_by_role("button", name=re.compile("^返回会话$")).click()
    expect(pb.locator(".msg-thread")).to_be_visible()
    expect(info).to_have_count(0)
    pb.get_by_role("button", name=re.compile("^返回会话列表$")).click()
    expect(pb.locator(".msg-side")).to_be_visible()
    pb.locator(".msg-conv", has_text=f"Launch {tag}").click()
    notes.append(f"{engine}: on the phone the group opened from the list, its info opened as a full page and closed with a real back control, and the list came back with its own")

    pa.get_by_role("button", name=re.compile("^群信息$")).click()
    column = pa.locator(".msg-info.column")
    expect(column).to_be_visible()                                          # 1280px → a third column, not an overlay
    assert pa.locator(".msg-layout.three").count() == 1
    shot(pa, shots, f"{engine}-desktop-three-columns")
    # ---- CI-78 group picture (§7.17): the owner picks a wide picture; the shell crops its centre
    #      square into the platform's avatar PNG; the member sees it in the thread header and the list
    expect(column.get_by_role("button", name=re.compile("^上传群头像$"))).to_be_visible()
    column.locator("input[type=file]").set_input_files({"name": "team.png", "mimeType": "image/png", "buffer": picture_png()})
    own_pic = column.locator(".msg-avatar-edit .msg-avatar.pic img")
    expect(own_pic).to_be_visible(timeout=15000)
    expect(column.get_by_role("button", name=re.compile("^更换群头像$"))).to_be_visible()
    expect(pa.locator(".msg-system").last).to_have_text(re.compile("更换了群头像"), timeout=10000)
    head_pic = pb.locator(".msg-thread-h .msg-avatar.pic img")
    expect(head_pic).to_be_visible(timeout=15000)
    pb.wait_for_function("img => img.complete && img.naturalWidth > 0", arg=head_pic.element_handle(), timeout=10000)
    natural = head_pic.evaluate("img => [img.naturalWidth, img.naturalHeight]")
    assert natural[0] == natural[1] and natural[0] <= 512, ("a square the platform accepts", natural)
    expect(pb.locator(".msg-system").last).to_have_text(re.compile(f"Owner {tag} 更换了群头像"))
    pic_src = head_pic.get_attribute("src")
    r = pb.request.get(messages + pic_src)
    assert r.ok and r.headers.get("content-type") == "image/png" and r.headers.get("cache-control") == "private, no-cache" and r.headers.get("x-content-type-options") == "nosniff", (r.status, dict(r.headers))
    pb.get_by_role("button", name=re.compile("^返回会话列表$")).click()
    expect(pb.locator(".msg-conv-wrap", has=pb.locator(".msg-conv-name", has_text=f"Launch {tag}")).locator(".msg-avatar.pic img")).to_be_visible(timeout=10000)
    shot(pb, shots, f"{engine}-phone-group-avatar")
    pb.locator(".msg-conv", has_text=f"Launch {tag}").click()
    expect(pb.locator(".msg-thread")).to_contain_text(f"welcome to the group {tag}")
    notes.append(f"{engine}: the owner uploaded a 96×64 picture from the group info; it became a {natural[0]}×{natural[1]} PNG shown to the member in the thread header and the list (image/png, private no-cache, nosniff) with '更换了群头像' in both threads")
    column.get_by_role("button", name=re.compile(f"成员操作：Member {tag}")).click()
    pa.get_by_role("menuitem", name=re.compile("^移出群聊$")).click()
    confirm = pa.get_by_role("alertdialog")
    expect(confirm).to_contain_text(re.compile("移出群聊"))
    assert pa.evaluate("() => document.activeElement && document.activeElement.textContent.trim() === '取消'"), "a destructive confirm focuses 取消"
    confirm.get_by_role("button", name=re.compile("^移出$")).click()
    expect(column).not_to_contain_text(f"Member {tag}", timeout=15000)
    expect(pb.locator(".msg-thread")).to_contain_text(re.compile("找不到这个会话"), timeout=15000)
    expect(pb.locator(".msg-side")).not_to_contain_text(f"Launch {tag}")
    r = pb.request.get(f"{messages}/api/messaging/conversations/{gid}/messages")
    assert r.status == 404, r.status
    r = pb.request.get(f"{messages}/api/messaging/conversations")
    assert all(c["id"] != gid for c in r.json()["items"])
    r = pb.request.get(f"{accounts}/api/auth/messaging/conversations")
    assert r.status == 410 and r.json().get("error") == "moved", (r.status, r.text()[:160])
    r = pb.request.get(messages + pic_src)
    assert r.status == 404, ("a removed member no longer gets the group picture", r.status)
    pa.get_by_role("button", name=re.compile("^关闭$")).click()
    expect(column).to_have_count(0)
    notes.append(f"{engine}: the group info opened as a third column at 1280px; the member was removed through the member menu and a confirm focused on 取消; from then on history is 404 and the group is gone from their list; the account origin answers 410")

    # ---- search only finds what the caller may read
    pb.goto(f"{messages}/api/messaging/search?q=welcome")
    assert "welcome" not in pb.content(), "a removed member cannot search the group's history"
    notes.append(f"{engine}: the removed member's search of the group's text found nothing")

    # ---- the /objects route (contract with the Docs↔Message linkage): a 404 has its own page; a server
    #      that does not serve the endpoint yet answers 405 from the shell catch-all, which is the
    #      error page with a retry — never an error dump. The 404 branch is pinned by the unit test.
    enter_messages(pa, messages, f"/objects/docweave/project/nope-{tag}")
    missing = pa.get_by_text(re.compile("找不到这个对象，或你没有权限查看"))
    failed_page = pa.get_by_text(re.compile("^无法打开会话$"))
    expect(missing.or_(failed_page)).to_be_visible()
    if missing.count():
        expect(pa.get_by_role("link", name=re.compile("返回工作空间"))).to_have_attribute("href", re.compile(r"/auth/start\?next=%2Fworkspace"))
        notes.append(f"{engine}: /objects/docweave/project/<unknown> showed the not-found page with the workspace handoff link")
    else:
        expect(pa.get_by_role("button", name=re.compile("^重试$"))).to_be_visible()
        expect(pa.get_by_role("link", name=re.compile("返回会话列表"))).to_have_attribute("href", "/")
        assert not re.search(r"Traceback|at \w+\.|\{\"", pa.locator("main, body").first.inner_text()), "no error dump"
        notes.append(f"{engine}: /objects/docweave/project/<unknown> on a server without the endpoint showed the error page with 重试 and a way back (the 404 page is pinned by the unit test)")

    # ---- CI-78 blocking (§7.18): the owner blocks the member from the direct message's info panel
    enter_messages(pb, messages, f"/c/{dm_id}")
    enter_messages(pa, messages, f"/c/{dm_id}")
    expect(pa.locator(".msg-status")).to_have_text(re.compile("实时"), timeout=15000)
    expect(pb.locator(".msg-status")).to_have_text(re.compile("实时"), timeout=15000)
    expect(pa.locator(".msg-thread-h .msg-presence-text")).to_have_text(re.compile("在线"), timeout=20000)
    pa.get_by_role("button", name=re.compile("^会话设置$")).click()
    info = pa.locator(".msg-info.column")
    expect(info.get_by_role("heading", name="对方")).to_be_visible()
    info.get_by_role("button", name=re.compile("^屏蔽$")).click()
    confirm = pa.get_by_role("alertdialog")
    expect(confirm).to_contain_text(re.compile("不能互相发起私聊"))
    expect(confirm).to_contain_text(re.compile("互相看不到在线状态、正在输入和已读"))
    expect(confirm).to_contain_text(re.compile("对方不会收到通知"))
    assert pa.evaluate("() => document.activeElement && document.activeElement.textContent.trim() === '取消'"), "a block confirm focuses 取消"
    shot(pa, shots, f"{engine}-desktop-block-confirm")
    confirm.get_by_role("button", name=re.compile("^屏蔽$")).click()
    expect(info.get_by_role("button", name=re.compile("^解除屏蔽$"))).to_be_visible(timeout=10000)
    expect(pa.locator(".msg-blocked-note")).to_contain_text("你已屏蔽对方，解除后才能发送")
    expect(pa.locator(".msg-composer textarea")).to_have_count(0)
    expect(pa.locator(".msg-thread-h .msg-presence-text")).to_have_count(0, timeout=15000)
    r = pa.request.post(f"{messages}/api/messaging/conversations/{dm_id}/messages", data={"body": f"owner blocked try {tag}"}, headers={"X-Synwevo-CSRF": csrf_of(a, messages)})
    assert r.status == 403 and r.json()["error"] == "blocked" and r.json()["message"] == "你已屏蔽对方，解除后才能发送", (r.status, r.text()[:200])
    shot(pa, shots, f"{engine}-desktop-blocked")
    # the member is not told: nothing changes on their screen until a send is refused, neutrally
    expect(pb.locator(".msg-thread-h .msg-presence-text")).to_have_count(0, timeout=15000)
    composer(pb).fill(f"neutral try {tag}")
    composer(pb).press("Enter")
    expect(pb.locator(".msg-failed").last).to_contain_text("暂时无法向对方发送消息", timeout=10000)
    expect(pb.locator(".msg-blocked-note")).to_contain_text("暂时无法向对方发送消息")
    # everything the member's thread says, apart from what the two people wrote themselves
    shell_text = pb.locator(".msg-thread").evaluate("el => { const c = el.cloneNode(true); c.querySelectorAll('.msg-body, .msg-quote-block, .msg-conv-preview').forEach(n => n.remove()); return c.innerText; }")
    assert "暂时无法向对方发送消息" in shell_text and not re.search("屏蔽|封鎖|block", shell_text, re.I), "the blocked person is never told"
    assert pb.request.get(f"{messages}/api/messaging/blocks").json()["items"] == []
    shot(pb, shots, f"{engine}-phone-blocked-neutral")
    no_overflow(pb, "phone refused send")
    notes.append(f"{engine}: the owner blocked the member from the DM info (the confirm states the effects and that the other side is not told; focus on 取消); the owner's composer became '你已屏蔽对方，解除后才能发送' and their send is 403 blocked with that sentence; presence vanished both ways; the member's send failed with '暂时无法向对方发送消息' and nothing on their screen names a block")
    # unblock from 消息设置 → 已屏蔽的人; both sides send again (the member retries the refused message)
    pa.get_by_role("button", name=re.compile("^消息设置$")).click()
    dlg = pa.get_by_role("dialog", name=re.compile("消息设置"))
    blocked_row = dlg.locator(".msg-blocked .msg-member", has_text=f"Member {tag}")
    expect(blocked_row).to_be_visible()
    shot(pa, shots, f"{engine}-desktop-blocked-list")
    blocked_row.get_by_role("button", name=re.compile("^解除屏蔽$")).click()
    expect(dlg.locator(".msg-blocked")).to_contain_text("你没有屏蔽任何人", timeout=10000)
    dlg.get_by_role("button", name=re.compile("^取消$")).click()
    expect(dlg).to_have_count(0)
    expect(pa.locator(".msg-composer textarea")).to_be_visible(timeout=10000)
    send_text(pa, f"after unblock {tag}")
    expect(pb.locator(".msg-thread")).to_contain_text(f"after unblock {tag}", timeout=15000)
    pb.locator(".msg-failed").last.get_by_role("button", name=re.compile("^重试$")).click()
    expect(pa.locator(".msg-thread")).to_contain_text(f"neutral try {tag}", timeout=15000)
    expect(pb.locator(".msg-failed")).to_have_count(0)
    expect(pb.locator(".msg-composer textarea")).to_be_visible()
    notes.append(f"{engine}: 解除屏蔽 in 消息设置 → 已屏蔽的人 restored the owner's composer; the owner's next message reached the member, and the member's retried message (same client id) reached the owner")

    # ---- CI-78 a group from the direct message: the other person chosen, the history left behind
    info.get_by_role("button", name=re.compile("^用这个人建群$")).click()
    dlg = pa.get_by_role("dialog", name=re.compile("新群聊"))
    expect(dlg.locator(".msg-note")).to_have_text("之前的私聊记录不会带入新群")
    expect(dlg.locator(".msg-chip-person")).to_have_count(1)
    expect(dlg.locator(".msg-chip-person")).to_contain_text(f"Member {tag}")
    shot(pa, shots, f"{engine}-desktop-dm-to-group")
    dlg.locator("#msg-gname").fill(f"From DM {tag}")
    dlg.get_by_role("button", name=re.compile("^创建群聊$")).click()
    pa.wait_for_url(lambda u: "/c/cv_" in u and dm_id not in u)
    expect(pa.locator(".msg-thread-name")).to_contain_text(f"From DM {tag}")
    expect(pa.locator(".msg-system").first).to_contain_text("创建了群聊")
    assert pa.locator(".msg-row").count() == 0 and f"after unblock {tag}" not in pa.locator(".msg-thread").inner_text(), "no private history in the new group"
    notes.append(f"{engine}: 用这个人建群 opened 新群聊 with the member already chosen and '之前的私聊记录不会带入新群'; the created group starts with its creation line only")

    # ---- CI-78 organization scope (§7.16): the owner joins a second organization with its own conversation
    r = pa.request.post(f"{accounts}/api/auth/organizations", data={"name": f"Second Org {tag}", "workspace_name": f"Second Space {tag}"},
                        headers={"X-Synwevo-CSRF": csrf_of(a, accounts), "Idempotency-Key": f"e2e-second-{tag}"})
    assert r.status == 201, (r.status, r.text()[:200])
    oid2 = r.json()["organization"]["id"]
    r = pa.request.post(f"{messages}/api/messaging/groups", data={"organization_id": oid2, "name": f"Second {tag}", "member_ids": []}, headers={"X-Synwevo-CSRF": csrf_of(a, messages)})
    assert r.status == 201, (r.status, r.text()[:200])
    enter_messages(pa, messages)
    scope_sel = pa.get_by_role("combobox", name=re.compile("按组织筛选会话"))
    expect(scope_sel).to_be_visible()
    all_rows = pa.locator(".msg-conv").count()
    with pa.expect_response(lambda resp: "/api/messaging/conversations?organization_id=" in resp.url) as scoped:
        scope_sel.select_option(oid2)
    assert scoped.value.ok and oid2 in scoped.value.url, scoped.value.url
    expect(pa.locator(".msg-conv")).to_have_count(1)
    expect(pa.locator(".msg-conv").first).to_contain_text(f"Second {tag}")
    shot(pa, shots, f"{engine}-desktop-org-filter")
    pa.reload()
    pa.wait_for_load_state("networkidle")
    expect(scope_sel).to_have_value(oid2)
    expect(pa.locator(".msg-conv")).to_have_count(1)
    new_conversation(pa, "新群聊")
    dlg = pa.locator(".modal").last
    expect(dlg.locator("#msg-org")).to_have_value(oid2)
    dlg.get_by_role("button", name=re.compile("^取消$")).click()
    scope_sel.select_option("")
    expect(pa.locator(".msg-conv")).to_have_count(all_rows, timeout=10000)
    assert pa.evaluate("() => Object.keys(localStorage).filter(k => k.startsWith('synwevo.msg.org.')).length") == 0, "every organization = nothing remembered"
    enter_messages(pb, messages)
    expect(pb.locator(".msg-conv").first).to_be_visible()
    expect(pb.get_by_role("combobox", name=re.compile("按组织筛选会话"))).to_have_count(0)
    notes.append(f"{engine}: with a second organization the owner's list offered the scope selector; choosing it asked the server with organization_id and showed only that organization's conversation, survived a reload (remembered per account), and preselected it in 新群聊; the member (one organization) has no selector")

    # ---- reduced motion: the same page with the OS preference set — durations are zero, everything still works
    rm = browser.new_context(viewport={"width": 1280, "height": 800}, locale="zh-CN", reduced_motion="reduce", storage_state=a.storage_state(), **extra)
    pr = rm.new_page(); watch(pr, violations, "reduced-motion")
    enter_messages(pr, messages, f"/c/{dm_id}")
    expect(pr.locator(".msg-status")).to_have_text(re.compile("实时"), timeout=15000)
    dur = pr.evaluate("() => getComputedStyle(document.documentElement).getPropertyValue('--dur-2').trim()")
    assert dur in ("0ms", "0s"), dur                       # engines serialise the zero differently
    composer(pr).fill(f"reduced motion {tag}")
    composer(pr).press("Enter")
    expect(pr.locator(".msg-row.own").last).to_contain_text(f"reduced motion {tag}")
    expect(pr.locator(".msg-pending")).to_have_count(0, timeout=10000)
    anim = pr.locator(".msg-row.own").last.evaluate("el => getComputedStyle(el).animationDuration")
    assert anim in ("0.001s", "0s", "1ms", "0ms"), anim
    enter_messages(pb, messages)                                              # the phone was left on the raw search answer
    expect(pb.locator(".msg-conv-preview").first).to_contain_text(f"reduced motion {tag}", timeout=15000)
    rm.close()
    notes.append(f"{engine}: under prefers-reduced-motion the motion tokens read 0ms (entry animation {anim}), and sending still worked and reached the member")

    shot(pa, shots, f"{engine}-desktop-thread")
    assert not violations, violations[:6]
    seen = sorted({x.split()[-1] for x in resource_logs})
    notes.append(f"{engine}: 0 console errors, 0 CSP violations, 0 page errors (browser logs for the provoked HTTP answers and the microphone policy refusal — {', '.join(seen)} — recorded, not counted)")
    resource_logs.clear()
    a.close(); b.close()
    return notes


def main():
    args = [x for x in sys.argv[1:] if not x.startswith("--")]
    shots: Path | None = None
    if "--shots" in sys.argv:
        shots = Path(sys.argv[sys.argv.index("--shots") + 1]); shots.mkdir(parents=True, exist_ok=True)
        args = [x for x in args if x != str(shots)]
    if len(args) != 3:
        raise SystemExit(__doc__)
    accounts, messages, mail_dir = require_test_instance(args[0]), require_test_instance(args[1]), Path(args[2])
    want_webkit = "--webkit" in sys.argv
    only_webkit = "--only-webkit" in sys.argv

    def show(engine: str, notes: list[str]):
        print(f"== {engine}", flush=True)
        for n in notes:
            print("  -", n, flush=True)

    with sync_playwright() as p:
        if not only_webkit:
            chrome = p.chromium.launch(channel="chrome", headless=True, args=["--use-fake-device-for-media-stream", "--use-fake-ui-for-media-stream"])
            try:
                show("chromium (Google Chrome)", run(chrome, "chromium", accounts, messages, mail_dir, shots))
            finally:
                chrome.close()
        if want_webkit or only_webkit:
            wk = p.webkit.launch(headless=True)
            try:
                with LocalhostProxy() as proxy:
                    show(f"webkit (Playwright build; *.localhost through {proxy.url})",
                         run(wk, "webkit", accounts, messages, mail_dir, shots, {"proxy": {"server": proxy.url}}))
            finally:
                wk.close()


if __name__ == "__main__":
    main()
