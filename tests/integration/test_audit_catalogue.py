"""The account service's copy of the applications' audit actions equals the applications' own
lists (admin coverage G26, decision D7 §9).

The repository boundary keeps the account service from importing the applications, so the labels
live there as data; this is the one place that may import all three and say whether they agree.
"""
from __future__ import annotations

from synwevo_docs.audit_actions import ACTIONS as DOCS_ACTIONS
from synwevo_identity.app_actions import APP_ACTIONS, APP_SCOPES, catalogue
from synwevo_suite.common.audit_actions import ACTIONS as SUITE_ACTIONS


def test_the_account_service_lists_exactly_the_actions_the_applications_write():
    written = set(DOCS_ACTIONS) | set(SUITE_ACTIONS)
    assert set(APP_ACTIONS) == written, {"unlabelled": sorted(written - set(APP_ACTIONS)),
                                         "stale": sorted(set(APP_ACTIONS) - written)}
    assert not set(DOCS_ACTIONS) & set(SUITE_ACTIONS)


def test_every_application_action_is_scoped_and_labelled_in_three_languages():
    for code, item in APP_ACTIONS.items():
        assert item["scope"] in APP_SCOPES and item["scope"].split(".")[0] == code.split(".")[0], code
        for locale in ("zh-Hans", "zh-Hant", "en"):
            assert item["label"][locale].strip(), (code, locale)
    body = catalogue()
    assert [i["code"] for i in body["items"]] == sorted(APP_ACTIONS)
    assert {s["id"] for s in body["scopes"]} == set(APP_SCOPES)
