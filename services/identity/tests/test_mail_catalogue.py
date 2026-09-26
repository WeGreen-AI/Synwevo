"""Every mail the account service sends, in every language, on the shared layout: the HTML and
the text carry the same title, links and code; dynamic text is escaped; links are absolute on the
configured public origin; the brand image is a public path on that origin; nothing renders as
None or a placeholder."""
from __future__ import annotations

import html as _html

import pytest

from synwevo_identity import mail
from synwevo_identity.mail_layout import Mail, render

LOCALES = ("zh-Hans", "zh-Hant", "en")


@pytest.fixture(autouse=True)
def _origin(monkeypatch):
    monkeypatch.setenv("SYNWEVO_PUBLIC_URL", "https://accounts.example.test")


def catalogue(locale: str) -> dict[str, tuple[str, str, str]]:
    return {
        "login_code": mail.login_code_mail(code="123456", expires_min=10, locale=locale),
        "invitation": mail.invitation_mail(workspace="Acme <b>Team</b>", inviter="Ann & Bob", role_label="编辑成员", token="tok-INV", expires_h=72, locale=locale),
        "verify": mail.verify_mail(token="tok-VER", expires_h=24, locale=locale),
        "account_exists": mail.account_exists_mail(locale=locale),
        "reset": mail.reset_mail(token="tok-RST", expires_h=2, locale=locale),
        "security": mail.security_notice(what="口令已更改", when_text="刚刚", locale=locale),
        "email_change": mail.email_change_mail(token="tok-CHG", expires_h=1, locale=locale),
        "account_deletion": mail.deletion_mail(locale, "https://accounts.example.test/account/recovery?lang=zh-Hans#token=tok-DEL"),
    }


@pytest.mark.parametrize("locale", LOCALES)
def test_every_mail_renders_text_and_html_consistently(locale):
    for name, (subject, text, html) in catalogue(locale).items():
        assert subject and "\n" not in subject, name
        assert text.startswith(subject), (name, text[:60])
        assert "None" not in text and "{" not in text and "undefined" not in text, (name, text)
        assert "<html" in html and 'lang="%s"' % locale in html and "<script" not in html, name
        assert 'src="https://accounts.example.test/brand/email-logo.png"' in html and 'alt="Synwevo"' in html, name
        # every https link in the text part is in the HTML part too, and nothing points elsewhere
        links = [w for w in text.split() if w.startswith("http")]
        assert links or name == "login_code", name                 # a code mail deliberately carries no link
        for link in links:
            assert link.startswith("https://accounts.example.test/"), (name, link)
            assert _html.escape(link, quote=True) in html, (name, link)
        if name == "login_code":
            assert "123456" in text and ">123456<" in html
            assert not links or all("token" not in l for l in links), "a code mail carries no bearer link"


def test_dynamic_text_is_escaped_in_html_and_verbatim_in_text():
    subject, text, html = mail.invitation_mail(workspace="Acme <b>Team</b>", inviter="Ann & Bob", role_label="编辑成员", token="t", expires_h=72, locale="zh-Hans")
    assert "Acme <b>Team</b>" in text and "Ann & Bob" in text
    assert "<b>Team</b>" not in html and "Acme &lt;b&gt;Team&lt;/b&gt;" in html and "Ann &amp; Bob" in html
    assert "Acme <b>Team</b>" in subject                      # the subject is a header, escaped by the MIME layer, never HTML


def test_layout_without_a_public_origin_falls_back_to_a_text_brand(monkeypatch):
    monkeypatch.delenv("SYNWEVO_PUBLIC_URL", raising=False)
    text, html = render(Mail("en", "Hello", ["A line."]))
    assert "email-logo.png" not in html and ">Synwevo<" in html and text.startswith("Hello")


def test_digest_items_and_facts_render_in_both_parts():
    m = Mail("zh-Hant", "3 則新通知", items=[{"title": "A 提到了你", "body": "在專案「甲」", "href": "https://accounts.example.test/x"}, {"title": "任務完成", "body": "", "href": ""}],
             facts=[("工作空間", "Team A")])
    text, html = render(m)
    assert "• A 提到了你" in text and "開啟: https://accounts.example.test/x" in text and "工作空間：Team A" in text
    assert 'href="https://accounts.example.test/x"' in html and "任務完成" in html and "Team A" in html
