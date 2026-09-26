"""Render every account-service mail in the three languages to HTML and text files, from the real
template code, for the record and for a look in a browser or a mail client. Synthetic data only;
no mail is sent.

    .venv/bin/python services/identity/scripts/mail_preview.py <out dir> [--public-url https://accounts.example.test]
"""
import os, sys
from pathlib import Path

out = Path(sys.argv[1]); out.mkdir(parents=True, exist_ok=True)
public = next((a.split("=", 1)[1] for a in sys.argv if a.startswith("--public-url=")), "https://accounts.example.test")
os.environ["SYNWEVO_PUBLIC_URL"] = public
from synwevo_identity import mail                      # noqa: E402  (after the origin is set)
from synwevo_identity.mail_layout import Mail, render  # noqa: E402

def catalogue(locale):
    return {
        "login_code": mail.login_code_mail(code="482913", expires_min=10, locale=locale),
        "invitation": mail.invitation_mail(workspace="示例工作空间 Example", inviter="示例邀请人 Example Inviter", role_label="编辑成员", token="SYNTHETIC-INVITE-TOKEN", expires_h=72, locale=locale),
        "verify": mail.verify_mail(token="SYNTHETIC-VERIFY-TOKEN", expires_h=24, locale=locale),
        "account_exists": mail.account_exists_mail(locale=locale),
        "reset": mail.reset_mail(token="SYNTHETIC-RESET-TOKEN", expires_h=2, locale=locale),
        "security": mail.security_notice(what="口令已更改", when_text="刚刚", locale=locale),
        "email_change": mail.email_change_mail(token="SYNTHETIC-CHANGE-TOKEN", expires_h=1, locale=locale),
        "account_deletion": mail.deletion_mail(locale, "https://accounts.example.test/account/recovery?lang=zh-Hans#token=SYNTHETIC-RECOVERY-TOKEN"),
        "notification": (None,) + render(Mail(locale, {"zh-Hans": "示例组织 邀请你加入工作空间「示例空间」", "zh-Hant": "示例組織 邀請你加入工作空間「示例空間」", "en": "Example Org invited you to the workspace Example"}[locale],
                                             [{"zh-Hans": "以「编辑成员」身份。", "zh-Hant": "以「編輯成員」身分。", "en": "As an editor."}[locale]],
                                             action=({"zh-Hans": "查看", "zh-Hant": "查看", "en": "Open"}[locale], f"{public}/invite?lang={locale}"))),
        "digest": (None,) + render(Mail(locale, {"zh-Hans": "Synwevo：3 条新通知", "zh-Hant": "Synwevo：3 則新通知", "en": "Synwevo: 3 new notifications"}[locale],
                                        items=[{"title": "示例成员 在项目「示例项目」的评论中提到了你", "body": "这个字段取哪一列？", "href": f"{public}/p/p_example?comment=cm_example"},
                                               {"title": "任务「示例项目」已完成：12 份成功", "body": "", "href": f"{public}/p/p_example/jobs/j_example"},
                                               {"title": "示例邀请人 邀请你加入工作空间「示例空间」", "body": "以「编辑成员」身份。", "href": f"{public}/invite"}])),
    }

index = ["<!DOCTYPE html><meta charset=utf-8><title>Synwevo mail previews</title><h1>Synwevo mail previews (synthetic data)</h1><ul>"]
for locale in ("zh-Hans", "zh-Hant", "en"):
    for name, (subject, text, html) in catalogue(locale).items():
        (out / f"{name}.{locale}.html").write_text(html, encoding="utf-8")
        (out / f"{name}.{locale}.txt").write_text((f"Subject: {subject}\n\n" if subject else "") + text, encoding="utf-8")
        index.append(f'<li><a href="{name}.{locale}.html">{name} · {locale}</a> — <a href="{name}.{locale}.txt">text</a></li>')
index.append("</ul>")
(out / "index.html").write_text("\n".join(index), encoding="utf-8")
print(f"wrote {len(list(out.glob('*.html'))) - 1} HTML previews and their text parts to {out}")
