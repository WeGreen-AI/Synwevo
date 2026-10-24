"""The public pages tell the truth about AI, whether the Agent is off or on (owner decision
2026-10-05: name DeepSeek publicly). Isolated fixture data; no model or network call.

* No public source still says that no AI processes content.
* The privacy page names the model provider in all three languages; the versioned documents
  print the versions of synwevo_core.terms, and the shell carries the same values.
* The deployment facts read the Agent's release from the application registry and whether its
  model is configured, so the live status follows the deployment instead of a sentence.
"""
from __future__ import annotations

import re
from pathlib import Path

from fastapi.testclient import TestClient

from synwevo_core.apps import bootstrap_suite_app
from synwevo_core.terms import DOCUMENTS

from .test_portal_public import portal  # noqa: F401 — fixture

ROOT = Path(__file__).resolve().parents[2]
WEB = ROOT / "apps/portal/web/src"
STALE = ("没有任何 AI 处理", "沒有任何 AI 處理", "no AI processes", "No AI processes")


def test_no_public_source_claims_that_no_ai_processes_content():
    from synwevo_portal.public import COPY
    sources = {p.name: p.read_text("utf-8") for p in WEB.glob("*.ts*")}
    sources["public.py"] = repr(COPY)
    for name, text in sources.items():
        for claim in STALE:
            assert claim not in text, (name, claim)


def test_the_privacy_copy_names_the_model_provider_in_three_languages():
    from synwevo_portal.public import COPY
    for locale in ("zh-Hans", "zh-Hant", "en"):
        text = " ".join(COPY[locale]["pages"]["privacy"]["paragraphs"])
        assert "Synwevo Agent" in text and "DeepSeek" in text, locale
    pages = (WEB / "PublicPages.tsx").read_text("utf-8")
    assert "杭州深度求索人工智能基础技术研究有限公司（DeepSeek）" in pages
    catalogue = (WEB / "messages.ts").read_text("utf-8")
    assert "Hangzhou DeepSeek Artificial Intelligence Co., Ltd. (DeepSeek)" in catalogue
    assert "杭州深度求索人工智能基礎技術研究有限公司（DeepSeek）" in catalogue


def test_versioned_documents_print_the_versions_of_record():
    from synwevo_portal.public import COPY
    routes = (WEB / "publicRoutes.ts").read_text("utf-8")
    for document, (version, effective) in DOCUMENTS.items():
        for locale in ("zh-Hans", "zh-Hant", "en"):
            first = COPY[locale]["pages"][document]["paragraphs"][0]
            assert version in first and effective in first and "{" not in first, (document, locale)
        shell = re.search(rf'"?{re.escape(document)}"?: \{{ version: "([^"]+)", effective: "([^"]+)" \}}', routes)
        assert shell and shell.groups() == (version, effective), document


#: Promises the public pages must no longer make (owner decision 2026-10-23, "send-and-go"): a
#: person sends and the Agent starts; nothing is confirmed per run or re-confirmed after a price
#: change. What every page must say instead: each answer has a maximum amount.
BANNED_PROMISES = ("确认最高预算", "确认后才会扣费", "需要重新确认", "你确认的上限",
                   "確認最高預算", "重新確認", "confirm a maximum", "confirm again", "maximum you confirmed")
PER_ANSWER_MAXIMUM = ("每条回答的最高金额", "每則回答的最高金額", "maximum for each answer")


def test_public_pages_make_no_per_run_confirmation_promise():
    from synwevo_portal.public import COPY
    sources = {name: (WEB / name).read_text("utf-8")
               for name in ("PublicPages.tsx", "PublicHome.tsx", "messages.ts", "publicRoutes.ts")}
    for locale in ("zh-Hans", "zh-Hant", "en"):
        sources[f"public.py[{locale}]"] = repr(COPY[locale])
    for name, text in sources.items():
        for promise in BANNED_PROMISES:
            assert promise not in text, (name, promise)
    everything = "\n".join(sources.values())
    for statement in PER_ANSWER_MAXIMUM:
        assert statement in everything, statement


def test_privacy_page_calls_the_exported_quotes_run_authorizations():
    """Since send-and-go nobody sees a quote: the export's Agent quotes are the run
    authorizations (agent_records.export, authorization automatic/confirmed/consent). The privacy
    page names them as the account page does, in all three languages."""
    pages = (WEB / "PublicPages.tsx").read_text("utf-8")
    catalogue = (WEB / "messages.ts").read_text("utf-8")
    assert "数据同意、运行授权、接受的条款版本" in pages
    for phrase in ("数据同意、运行授权、接受的条款版本", "資料同意、執行授權、接受的條款版本",
                   "data consents, run authorizations, the terms versions you accepted"):
        assert phrase in catalogue, phrase
    for retired in ("数据同意、报价、", "資料同意、報價、", "data consents, quotes,"):
        assert retired not in pages and retired not in catalogue, retired


def test_deployment_facts_follow_the_agent_registry_and_model_configuration(portal, monkeypatch):  # noqa: F811
    from synwevo_portal.public import agent_fact, deployment_facts
    api, store, ident = portal
    monkeypatch.setenv("SYNWEVO_AGENT_PROVIDER", "deepseek")
    monkeypatch.delenv("SYNWEVO_BILLING_ENVIRONMENT", raising=False)
    assert agent_fact(store, ident) == {"released": False, "provider": "deepseek", "model_configured": False}
    bootstrap_suite_app(store, ident, app_id="agent", name="Synwevo Agent", icon="agent",
                        launch_url="https://portal.example.test/agent", released=True)
    assert agent_fact(store, ident)["released"] is True
    bootstrap_suite_app(store, ident, app_id="agent", name="Synwevo Agent", icon="agent",
                        launch_url="https://portal.example.test/agent", released=False)
    assert agent_fact(store, ident)["released"] is False
    monkeypatch.setenv("SYNWEVO_AGENT_PROVIDER", "anthropic")
    monkeypatch.setenv("SYNWEVO_AGENT_ANTHROPIC_API_KEY", "fixture-not-a-real-key")
    assert agent_fact(store, ident)["model_configured"] is True
    facts = deployment_facts(store, ident, force=True)
    assert set(facts["agent"]) == {"released", "provider", "model_configured"}
    served = TestClient(api).get("/public/deployment.json").json()
    assert "agent" in served and "fixture-not-a-real-key" not in str(served)
