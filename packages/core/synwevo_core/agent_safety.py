"""Content safety and the separate model-provider consent of the personal Agent.

Shared, because two services use it: the workplace origin enforces it on every Agent message and
model call (``synwevo_suite.agent``), the account service governs it (administrators publish the
rule lists, review reports, suspend a person's Agent access) and exports or erases a person's
records. Neither imports the other; both read and write these tables through the one Store.

What is stored is deliberately small: a stopped message is recorded as *which rule stopped it, in
which direction, and how long the text was* — never the text. A report keeps the reporter's
category and note, and an excerpt of the answer only when the reporter chose to include it.

The model-provider consent is versioned. A person sends nothing to a named provider (today only
DeepSeek) until they have accepted that provider's current version; a new version asks again;
withdrawing it stops every further model call, and the withdrawal is journalled so a restored
backup cannot bring the consent back (`journal.py`).
"""
from __future__ import annotations

import hashlib
import json
import re
import threading
import unicodedata
from dataclasses import dataclass
from re import _parser as _sre  # stdlib parser, present in 3.11–3.14

from .errors import ServiceError
from .store import Store, new_id, now


# ====================================================================== model-provider consent
@dataclass(frozen=True)
class ProviderTerms:
    """What a person agrees to before content is sent to a named model provider.

    ``version`` changes whenever the facts below or the dialog's wording change; a person who
    accepted an older version is asked again before the next model call."""
    provider: str
    version: str
    effective_at: str
    processor: dict
    data_location: str
    training: str          # what the provider's own policy says about use for model improvement
    retention: str


PROVIDER_TERMS: dict[str, ProviderTerms] = {
    "deepseek": ProviderTerms(
        provider="deepseek", version="2026-10-05", effective_at="2026-10-05",
        processor={"zh-Hans": "杭州深度求索人工智能基础技术研究有限公司（DeepSeek）",
                   "zh-Hant": "杭州深度求索人工智能基礎技術研究有限公司（DeepSeek）",
                   "en": "Hangzhou DeepSeek Artificial Intelligence Co., Ltd. (DeepSeek)"},
        # Owner decision 2026-10-05: until DeepSeek confirms otherwise in writing, say what its own
        # privacy policy allows — stored in China, possibly used to improve its models.
        data_location="CN", training="per_provider_policy", retention="per_provider_policy"),
}


def provider_terms(provider: str) -> ProviderTerms | None:
    return PROVIDER_TERMS.get(provider)


class ProviderConsents:
    """One consent per person and provider: the accepted version, or withdrawn."""

    def __init__(self, store: Store):
        self.store = store

    def status(self, user_id: str, provider: str) -> dict:
        terms = PROVIDER_TERMS.get(provider)
        if terms is None:
            raise ServiceError(404, "provider_consent_not_needed", "这个模型服务不需要单独同意")
        row = self.store.one("SELECT * FROM agent_provider_consents WHERE user_id=? AND provider=?", (user_id, provider))
        active = bool(row and row["withdrawn_at"] is None)
        return {"provider": provider, "version": terms.version, "effective_at": terms.effective_at,
                "processor": terms.processor, "data_location": terms.data_location,
                "training": terms.training, "retention": terms.retention,
                "accepted": active and row["version"] == terms.version,
                "accepted_version": row["version"] if active else None,
                "accepted_at": row["accepted_at"] if active else None,
                "withdrawn_at": row["withdrawn_at"] if row else None}

    def require(self, user_id: str, provider: str) -> None:
        """Raise unless the person's consent for ``provider`` covers its current version."""
        terms = PROVIDER_TERMS.get(provider)
        if terms is None:
            return
        row = self.store.one("SELECT version, withdrawn_at FROM agent_provider_consents WHERE user_id=? AND provider=?",
                             (user_id, provider))
        detail = {"provider": provider, "version": terms.version}
        if not row or row["withdrawn_at"] is not None:
            raise ServiceError(409, "provider_consent_required",
                               "使用 Agent 前，需要你单独同意把消息内容发送给模型提供方处理", detail)
        if row["version"] != terms.version:
            raise ServiceError(409, "provider_consent_version_changed",
                               "向模型提供方发送内容的说明已更新，请查看并同意新版本", detail)

    def accept(self, user_id: str, provider: str, version: str) -> dict:
        terms = PROVIDER_TERMS.get(provider)
        if terms is None:
            raise ServiceError(404, "provider_consent_not_needed", "这个模型服务不需要单独同意")
        if version != terms.version:
            raise ServiceError(409, "provider_consent_version_changed",
                               "向模型提供方发送内容的说明已更新，请查看并同意新版本",
                               {"provider": provider, "version": terms.version})
        with self.store.tx() as c:
            row = c.execute("SELECT version, withdrawn_at FROM agent_provider_consents WHERE user_id=? AND provider=?",
                            (user_id, provider)).fetchone()
            if not row or row["withdrawn_at"] is not None or row["version"] != version:
                ts = now()
                c.execute("INSERT INTO agent_provider_consents (user_id,provider,version,accepted_at,withdrawn_at) "
                          "VALUES (?,?,?,?,NULL) ON CONFLICT(user_id,provider) DO UPDATE SET version=excluded.version, "
                          "accepted_at=excluded.accepted_at, withdrawn_at=NULL", (user_id, provider, version, ts))
                c.execute("INSERT INTO agent_provider_consent_log (id,user_id,provider,version,action,at) "
                          "VALUES (?,?,?,?,'accepted',?)", (new_id("pcl"), user_id, provider, version, ts))
        return self.status(user_id, provider)

    def withdraw(self, user_id: str, provider: str) -> dict:
        """Stop every further model call to ``provider`` for this person. Journalled ahead of the
        change, so a restored backup cannot bring the consent back."""
        if provider not in PROVIDER_TERMS:
            raise ServiceError(404, "provider_consent_not_needed", "这个模型服务不需要单独同意")
        from . import journal
        with self.store.tx() as c:
            row = c.execute("SELECT version, withdrawn_at FROM agent_provider_consents WHERE user_id=? AND provider=?",
                            (user_id, provider)).fetchone()
            if row and row["withdrawn_at"] is None:
                ts = now()
                journal.record(self.store, "agent_provider_consent_withdrawn", user_id=user_id, provider=provider)
                c.execute("UPDATE agent_provider_consents SET withdrawn_at=? WHERE user_id=? AND provider=?",
                          (ts, user_id, provider))
                c.execute("INSERT INTO agent_provider_consent_log (id,user_id,provider,version,action,at) "
                          "VALUES (?,?,?,?,'withdrawn',?)", (new_id("pcl"), user_id, provider, row["version"], ts))
        return self.status(user_id, provider)

    def history(self, user_id: str) -> list[dict]:
        return self.store.all("SELECT provider, version, action, at FROM agent_provider_consent_log "
                              "WHERE user_id=? ORDER BY at, rowid", (user_id,))


# ====================================================================== governed rule lists
DIRECTIONS = ("input", "output", "both")
KINDS = ("keyword", "pattern")
CATEGORIES = ("illegal", "violence", "sexual_minors", "self_harm", "hate", "fraud", "privacy", "malware", "other")
MAX_RULES = 2000
MAX_PATTERN = 200
_INVISIBLE = dict.fromkeys(map(ord, "​‌‍⁠﻿­"))
_SPACE = re.compile(r"\s+")
_RULE_ID = re.compile(r"[A-Za-z0-9_.:-]{1,64}\Z")


def normalize(text: str) -> str:
    """What a keyword is compared against: compatibility-folded, case-folded, without invisible
    characters and with runs of white space as one space — so ``Ｆｏｏ``, ``f​oo`` and ``FOO``
    are the same word."""
    return _SPACE.sub(" ", unicodedata.normalize("NFKC", text).translate(_INVISIBLE).casefold()).strip()


def _nested_repeat(tree) -> bool:
    """True when a repeated group itself contains a repeat — the shape of catastrophic
    backtracking (``(a+)+``). Administrators' patterns run against every message."""
    sre = _sre

    def walk(items, inside):
        for op, av in items:
            if op in (sre.MAX_REPEAT, sre.MIN_REPEAT, getattr(sre, "POSSESSIVE_REPEAT", None)):
                if inside:
                    return True
                if walk(av[2], True):
                    return True
            elif op == sre.SUBPATTERN:
                if walk(av[-1], inside):
                    return True
            elif op == sre.BRANCH:
                if any(walk(b, inside) for b in av[1]):
                    return True
            elif op in (sre.ASSERT, sre.ASSERT_NOT):
                if walk(av[1], inside):
                    return True
        return False
    return walk(tree, False)


def validate_rules(rules) -> list[dict]:
    """The canonical form of an administrator's rule list, or a 400 naming the first bad rule."""
    if not isinstance(rules, list) or len(rules) > MAX_RULES:
        raise ServiceError(400, "safety_rules_invalid", "规则列表无效", {"limit": MAX_RULES})
    out, seen = [], set()
    for index, raw in enumerate(rules):
        def bad(field):
            raise ServiceError(400, "safety_rule_invalid", "这条规则无效，请检查", {"index": index, "field": field})
        if not isinstance(raw, dict) or set(raw) - {"id", "kind", "pattern", "applies_to", "category"}:
            bad("rule")
        kind = raw.get("kind", "keyword")
        if kind not in KINDS:
            bad("kind")
        applies = raw.get("applies_to", "both")
        if applies not in DIRECTIONS:
            bad("applies_to")
        category = raw.get("category", "other")
        if category not in CATEGORIES:
            bad("category")
        pattern = raw.get("pattern")
        if not isinstance(pattern, str) or not pattern.strip() or len(pattern) > MAX_PATTERN:
            bad("pattern")
        if kind == "keyword":
            pattern = normalize(pattern)
            if not pattern:
                bad("pattern")
        else:
            try:
                tree = _sre.parse(pattern)
                re.compile(pattern)
            except (re.error, RecursionError, OverflowError):
                bad("pattern")
            if _nested_repeat(tree):
                bad("pattern")
        rid = raw.get("id") or f"r{index + 1}"
        if not isinstance(rid, str) or not _RULE_ID.match(rid) or rid in seen:
            bad("id")
        seen.add(rid)
        out.append({"id": rid, "kind": kind, "pattern": pattern, "applies_to": applies, "category": category})
    return out


class _Compiled:
    def __init__(self, version: int, rules: list[dict]):
        self.version = version
        self.rules = rules
        self.patterns = {r["id"]: re.compile(r["pattern"], re.IGNORECASE) for r in rules if r["kind"] == "pattern"}

    def match(self, text: str, direction: str) -> dict | None:
        if not self.rules or not text:
            return None
        folded = normalize(text)
        for r in self.rules:
            if r["applies_to"] not in (direction, "both"):
                continue
            if r["kind"] == "keyword" and r["pattern"] in folded:
                return r
            if r["kind"] == "pattern" and (self.patterns[r["id"]].search(text) or self.patterns[r["id"]].search(folded)):
                return r
        return None


class SafetyRules:
    """The administrators' published keyword/pattern lists. Each publication is a new immutable
    version with its author and reason; the newest is in force."""

    #: Compiled lists by version and content digest. Keyed by what is compiled, never by the store
    #: object: a recycled id() of another (or a restored) database must not hand over its rules.
    _cache: dict[str, _Compiled] = {}
    _CACHE_MAX = 16
    _lock = threading.Lock()

    def __init__(self, store: Store):
        self.store = store

    def current(self) -> dict:
        row = self.store.one("SELECT * FROM agent_safety_rule_versions ORDER BY version DESC LIMIT 1")
        if not row:
            return {"version": 0, "rules": [], "actor_id": None, "reason": None, "created_at": None}
        return {"version": row["version"], "rules": json.loads(row["rules_json"]), "actor_id": row["actor_id"],
                "reason": row["reason"], "created_at": row["created_at"]}

    def history(self, limit: int = 20) -> list[dict]:
        return [{"version": r["version"], "rules": len(json.loads(r["rules_json"])), "actor_id": r["actor_id"],
                 "reason": r["reason"], "created_at": r["created_at"]}
                for r in self.store.all("SELECT * FROM agent_safety_rule_versions ORDER BY version DESC LIMIT ?", (limit,))]

    def publish(self, actor_id: str, rules, *, reason: str, expected_version: int | None = None) -> dict:
        reason = (reason or "").strip()
        if not 4 <= len(reason) <= 500:
            raise ServiceError(400, "missing_reason", "请写明理由（4 至 500 个字）")
        clean = validate_rules(rules)
        with self.store.tx() as c:
            row = c.execute("SELECT MAX(version) AS v FROM agent_safety_rule_versions").fetchone()
            version = (row["v"] or 0)
            if expected_version is not None and expected_version != version:
                raise ServiceError(409, "safety_rules_changed", "规则已被他人更新，请刷新后再改", {"version": version})
            c.execute("INSERT INTO agent_safety_rule_versions (version,rules_json,actor_id,reason,created_at) VALUES (?,?,?,?,?)",
                      (version + 1, json.dumps(clean, ensure_ascii=False, sort_keys=True), actor_id, reason, now()))
        return self.current()

    def compiled(self) -> _Compiled:
        row = self.store.one("SELECT version, rules_json FROM agent_safety_rule_versions ORDER BY version DESC LIMIT 1")
        if not row:
            return _Compiled(0, [])
        key = f'{row["version"]}:{hashlib.sha256(row["rules_json"].encode()).hexdigest()}'
        with self._lock:
            hit = self._cache.get(key)
            if hit is not None:
                return hit
        compiled = _Compiled(row["version"], json.loads(row["rules_json"]))
        with self._lock:
            while len(self._cache) >= self._CACHE_MAX:
                self._cache.pop(next(iter(self._cache)))
            self._cache[key] = compiled
        return compiled


class SafetyEvents:
    """That a rule stopped something — never what was stopped."""

    def __init__(self, store: Store):
        self.store = store

    def record(self, user_id: str, direction: str, *, rule: dict | None, version: int | None,
               conversation_id: str | None = None, turn_id: str | None = None, characters: int = 0,
               category: str | None = None) -> str:
        eid = new_id("sev")
        self.store.execute("INSERT INTO agent_safety_events (id,user_id,conversation_id,turn_id,direction,rule_version,"
                           "rule_id,category,characters,created_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
                           (eid, user_id, conversation_id, turn_id, direction, version,
                            rule["id"] if rule else None, (rule or {}).get("category") or category, int(characters), now()))
        return eid

    def summary(self, *, since: float, limit: int = 50) -> list[dict]:
        """Per person: how many stops since ``since`` — what an administrator triages."""
        return self.store.all("SELECT e.user_id, u.display_name, u.email, COUNT(*) AS stops, MAX(e.created_at) AS last_at "
                              "FROM agent_safety_events e LEFT JOIN users u ON u.id=e.user_id WHERE e.created_at>=? "
                              "GROUP BY e.user_id ORDER BY stops DESC, last_at DESC LIMIT ?", (since, limit))


# ====================================================================== reports
REPORT_CATEGORIES = ("harmful", "illegal", "privacy", "wrong", "other")
MAX_NOTE = 1000
MAX_EXCERPT = 2000


class AgentReports:
    def __init__(self, store: Store):
        self.store = store

    def create(self, user_id: str, *, conversation_id: str, message_id: str, category: str,
               note: str | None, excerpt: str | None) -> dict:
        if category not in REPORT_CATEGORIES:
            raise ServiceError(400, "report_category_invalid", "请选择举报原因")
        note = (note or "").strip()[:MAX_NOTE] or None
        excerpt = (excerpt or "")[:MAX_EXCERPT] or None
        rid = new_id("arp")
        try:
            self.store.execute("INSERT INTO agent_message_reports (id,user_id,conversation_id,message_id,category,note,excerpt,"
                               "status,created_at) VALUES (?,?,?,?,?,?,?,'open',?)",
                               (rid, user_id, conversation_id, message_id, category, note, excerpt, now()))
        except Exception as exc:  # noqa: BLE001 — UNIQUE(user_id,message_id)
            if "UNIQUE" not in str(exc):
                raise
            raise ServiceError(409, "report_exists", "你已经举报过这条回答") from None
        return self.get(rid)

    def get(self, report_id: str) -> dict:
        row = self.store.one("SELECT * FROM agent_message_reports WHERE id=?", (report_id,))
        if not row:
            raise ServiceError(404, "report_not_found", "举报不存在")
        return dict(row)

    def list(self, *, status: str | None, limit: int = 50) -> list[dict]:
        clause, args = ("WHERE r.status=?", (status,)) if status else ("", ())
        return self.store.all("SELECT r.*, u.display_name AS reporter_name, u.email AS reporter_email "
                              f"FROM agent_message_reports r LEFT JOIN users u ON u.id=r.user_id {clause} "
                              "ORDER BY r.created_at DESC LIMIT ?", (*args, limit))

    def resolve(self, report_id: str, actor_id: str, *, decision: str, resolution: str | None) -> dict:
        if decision not in ("resolved", "dismissed"):
            raise ServiceError(400, "report_decision_invalid", "请选择处理结果")
        current = self.get(report_id)
        if current["status"] != "open":
            raise ServiceError(409, "report_closed", "这条举报已经处理过了")
        self.store.execute("UPDATE agent_message_reports SET status=?, resolved_by=?, resolved_at=?, resolution=? WHERE id=?",
                           (decision, actor_id, now(), (resolution or "").strip()[:MAX_NOTE] or None, report_id))
        return self.get(report_id)


# ====================================================================== access suspension
class AgentAccess:
    """An administrator's suspension of one person's Agent access (with a reason). While it is in
    force no message is accepted and no model call is made for that person."""

    def __init__(self, store: Store):
        self.store = store

    def active(self, user_id: str) -> dict | None:
        return self.store.one("SELECT * FROM agent_access_suspensions WHERE user_id=? AND lifted_at IS NULL "
                              "ORDER BY created_at DESC LIMIT 1", (user_id,))

    def require(self, user_id: str) -> None:
        if self.active(user_id):
            raise ServiceError(403, "agent_access_suspended", "你的 Agent 使用权限已被管理员暂停，如有疑问请联系平台管理员")

    def suspend(self, user_id: str, actor_id: str, *, reason: str, report_id: str | None = None) -> dict:
        reason = (reason or "").strip()
        if not 4 <= len(reason) <= 500:
            raise ServiceError(400, "missing_reason", "请写明理由（4 至 500 个字）")
        from . import journal
        with self.store.tx():
            current = self.active(user_id)
            if current:
                return dict(current)
            sid = new_id("asp")
            # Write-ahead: a restored backup must not give the person their Agent back.
            journal.record(self.store, "agent_access_suspended", suspension_id=sid, user_id=user_id)
            self.store.execute("INSERT INTO agent_access_suspensions (id,user_id,reason,report_id,created_by,created_at) "
                               "VALUES (?,?,?,?,?,?)", (sid, user_id, reason, report_id, actor_id, now()))
            # Stop what is running now: the engine rechecks before every model call, and a
            # cancellation request ends a turn that is waiting for confirmation.
            self.store.execute("UPDATE agent_turns SET cancel_requested=1, updated_at=? WHERE user_id=? "
                               "AND status IN ('queued','running','waiting_confirmation')", (now(), user_id))
            return dict(self.store.one("SELECT * FROM agent_access_suspensions WHERE id=?", (sid,)))

    def lift(self, user_id: str, actor_id: str, *, reason: str) -> dict:
        reason = (reason or "").strip()
        if not 4 <= len(reason) <= 500:
            raise ServiceError(400, "missing_reason", "请写明理由（4 至 500 个字）")
        current = self.active(user_id)
        if not current:
            raise ServiceError(404, "agent_suspension_not_found", "这个账户的 Agent 没有被暂停")
        self.store.execute("UPDATE agent_access_suspensions SET lifted_at=?, lifted_by=?, lift_reason=? WHERE id=?",
                           (now(), actor_id, reason, current["id"]))
        return dict(self.store.one("SELECT * FROM agent_access_suspensions WHERE id=?", (current["id"],)))
