"""One layout for every Synwevo mail: the same header (the confirmed logo), title, plain
paragraphs, an optional code, one primary action with its address written out, a facts card,
a list of items, and the footer — as HTML for clients that render it and as the equivalent
plain text (the two are built from the same fields, so they cannot drift).

Rules this module keeps (docs/platform/mail-design.md):

* everything dynamic is escaped; names, organization names and project names are data;
* white card on a light page, brand blue only for the action and links; system fonts;
* single column, 600 px at most, tables for layout (email clients), no scripts, no background
  images; the logo is an `<img>` with alt text, so a client that blocks images still reads it;
* no dark-mode media query is relied on: the card has an explicit white background and the
  logo is a transparent PNG with dark ink, so automatic inversion keeps it legible;
* the action link is repeated as text under the button for clients that strip links.
"""
from __future__ import annotations

import html as _html
from dataclasses import dataclass, field

from synwevo_core import env

ACCENT = "#2563eb"
TEXT = "#0f172a"
MUTED = "#64748b"
LINE = "#e2e8f0"
PAGE = "#f5f7fb"

STRINGS = {
    "zh-Hans": {"open": "打开", "copy": "如果按钮无法点击，把这个地址复制到浏览器：", "auto": "这封邮件由 Synwevo 自动发送，回复不会有人处理。",
                "why": "你收到这封邮件，是因为这个邮箱在 Synwevo 上有账户或收到了邀请。", "code_hint": "验证码只在你自己打开的页面输入；Synwevo 不会通过电话或聊天索要它。"},
    "zh-Hant": {"open": "開啟", "copy": "若按鈕無法點按，請把這個網址複製到瀏覽器：", "auto": "這封郵件由 Synwevo 自動寄出，回覆不會有人處理。",
                "why": "你會收到這封郵件，是因為這個信箱在 Synwevo 上有帳戶或收到了邀請。", "code_hint": "驗證碼只在你自己開啟的頁面輸入；Synwevo 不會透過電話或聊天索取它。"},
    "en": {"open": "Open", "copy": "If the button does not work, copy this address into your browser:", "auto": "Synwevo sent this message automatically; replies are not read.",
           "why": "You received this because this address has a Synwevo account or was invited to one.", "code_hint": "Enter the code only on the page you opened yourself; Synwevo never asks for it by phone or chat."},
}


def loc(locale: str | None) -> str:
    return locale if locale in STRINGS else "zh-Hans"


def public_base() -> str:
    return (env.get("PUBLIC_URL") or "").strip().rstrip("/")


def logo_url() -> str:
    """The brand image for mail: a stable public path on the account service (no cookies, no
    fingerprint, HTTPS in production), so mail clients' image proxies can fetch it."""
    base = public_base()
    return f"{base}/brand/email-logo.png" if base else ""


@dataclass
class Mail:
    """What one mail says; `render` turns it into text and HTML."""
    locale: str
    title: str
    paragraphs: list[str] = field(default_factory=list)          # plain sentences, in order, before the action
    code: str | None = None                                       # a one-time code, shown large and selectable
    code_label: str = "验证码"                                     # names the code in the text part ("登录验证码：123456")
    action: tuple[str, str] | None = None                         # (label, absolute url)
    facts: list[tuple[str, str]] = field(default_factory=list)    # key/value card
    items: list[dict] = field(default_factory=list)               # digest rows: {title, body?, href?}
    after: list[str] = field(default_factory=list)                # sentences after the action (expiry, "if this was not you")
    security: bool = False                                        # security notices carry the "cannot be disabled" line


def e(s: object) -> str:
    return _html.escape(str(s), quote=True)


def render(m: Mail) -> tuple[str, str]:
    L = STRINGS[loc(m.locale)]
    # ---------------------------------------------------------------- text
    t: list[str] = [m.title, ""]
    for p in m.paragraphs:
        t += [p, ""]
    if m.code:
        t += [f"{m.code_label}：{m.code}" if loc(m.locale) != "en" else f"{m.code_label}: {m.code}", ""]
    for k, v in m.facts:
        t.append(f"{k}：{v}" if loc(m.locale) != "en" else f"{k}: {v}")
    if m.facts:
        t.append("")
    for it in m.items:
        t.append(f"• {it['title']}")
        if it.get("body"):
            t.append(f"  {it['body']}")
        if it.get("href"):
            t.append(f"  {L['open']}: {it['href']}")
        t.append("")
    if m.action:
        t += [f"{m.action[0]}：" if loc(m.locale) != "en" else f"{m.action[0]}:", f"  {m.action[1]}", ""]
    for p in m.after:
        t += [p, ""]
    t += [L["why"], L["auto"]]
    text = "\n".join(t).rstrip() + "\n"
    # ---------------------------------------------------------------- html
    h: list[str] = []
    h.append('<!DOCTYPE html><html lang="%s"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width">'
             '<meta name="color-scheme" content="light"><meta name="supported-color-schemes" content="light"><title>%s</title></head>' % (e(loc(m.locale)), e(m.title)))
    h.append(f'<body style="margin:0;padding:0;background:{PAGE};color:{TEXT};font-family:-apple-system,BlinkMacSystemFont,\'Segoe UI\',Roboto,\'PingFang SC\',\'Microsoft YaHei\',\'Noto Sans CJK SC\',sans-serif;font-size:15px;line-height:1.6;" bgcolor="{PAGE}">')
    h.append(f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="background:{PAGE};" bgcolor="{PAGE}"><tr><td align="center" style="padding:24px 12px;">')
    h.append('<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="max-width:600px;width:100%;">')
    logo = logo_url()
    if logo:
        h.append(f'<tr><td style="padding:0 4px 14px;"><img src="{e(logo)}" width="120" height="28" alt="Synwevo" style="display:block;width:120px;height:28px;border:0;"></td></tr>')
    else:
        h.append(f'<tr><td style="padding:0 4px 14px;font-weight:700;font-size:18px;color:{TEXT};">Synwevo</td></tr>')
    h.append(f'<tr><td style="background:#ffffff;border:1px solid {LINE};border-radius:12px;padding:28px 28px 22px;" bgcolor="#ffffff">')
    h.append(f'<h1 style="margin:0 0 14px;font-size:20px;line-height:1.35;font-weight:700;color:{TEXT};">{e(m.title)}</h1>')
    for p in m.paragraphs:
        h.append(f'<p style="margin:0 0 12px;color:{TEXT};">{e(p)}</p>')
    if m.code:
        h.append(f'<p style="margin:16px 0 6px;"><span style="display:inline-block;font-family:SFMono-Regular,Menlo,Consolas,monospace;font-size:28px;letter-spacing:0.18em;font-weight:700;color:{TEXT};background:{PAGE};border:1px solid {LINE};border-radius:8px;padding:10px 16px;">{e(m.code)}</span></p>')
        h.append(f'<p style="margin:0 0 12px;font-size:13px;color:{MUTED};">{e(L["code_hint"])}</p>')
    if m.facts:
        h.append(f'<table role="presentation" cellpadding="0" cellspacing="0" border="0" style="width:100%;margin:6px 0 14px;border:1px solid {LINE};border-radius:8px;border-collapse:separate;">')
        for k, v in m.facts:
            h.append(f'<tr><td style="padding:8px 12px;font-size:13px;color:{MUTED};white-space:nowrap;vertical-align:top;border-bottom:1px solid {LINE};">{e(k)}</td>'
                     f'<td style="padding:8px 12px;font-size:14px;color:{TEXT};border-bottom:1px solid {LINE};word-break:break-word;">{e(v)}</td></tr>')
        h.append('</table>')
    if m.items:
        h.append('<table role="presentation" cellpadding="0" cellspacing="0" border="0" style="width:100%;margin:4px 0 12px;">')
        for it in m.items:
            title = e(it["title"])
            if it.get("href"):
                title = f'<a href="{e(it["href"])}" style="color:{ACCENT};text-decoration:none;font-weight:600;">{title}</a>'
            body = f'<div style="font-size:13px;color:{MUTED};margin-top:2px;">{e(it["body"])}</div>' if it.get("body") else ""
            h.append(f'<tr><td style="padding:10px 0;border-bottom:1px solid {LINE};">{title}{body}</td></tr>')
        h.append('</table>')
    if m.action:
        label, url = m.action
        h.append(f'<table role="presentation" cellpadding="0" cellspacing="0" border="0" style="margin:18px 0 10px;"><tr><td style="border-radius:8px;background:{ACCENT};" bgcolor="{ACCENT}">'
                 f'<a href="{e(url)}" style="display:inline-block;padding:12px 22px;font-size:15px;font-weight:600;color:#ffffff;text-decoration:none;border-radius:8px;">{e(label)}</a></td></tr></table>')
        h.append(f'<p style="margin:0 0 14px;font-size:12px;color:{MUTED};word-break:break-all;">{e(L["copy"])}<br><a href="{e(url)}" style="color:{ACCENT};">{e(url)}</a></p>')
    for p in m.after:
        h.append(f'<p style="margin:0 0 10px;font-size:14px;color:{TEXT};">{e(p)}</p>')
    h.append('</td></tr>')
    h.append(f'<tr><td style="padding:16px 8px 0;font-size:12px;line-height:1.6;color:{MUTED};">{e(L["why"])}<br>{e(L["auto"])}</td></tr>')
    h.append('</table></td></tr></table></body></html>')
    return text, "".join(h)
