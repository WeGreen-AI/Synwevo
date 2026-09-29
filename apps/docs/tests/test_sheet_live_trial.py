"""The live-table switch scoped to workspaces: a limited trial (docs-sheet-live.md §9 and §15).

``SYNWEVO_SHEET_LIVE`` is ``off``, ``on`` or a comma-separated list of workspace ids. A table room of a
workspace that is not listed is CI-47's presence room on every server path that decides live mode —
joining, saving, the ``live_edits`` guard, the builtin PUT's ``live_room`` and the table drafts — while
a listed workspace's rooms are live in the same process.

TEST FIXTURES: the ``world`` of test_sheet_live (a workspace with an owner, an editor and a viewer, and
a stranger who owns a second workspace; real sessions in a temporary data directory) and the
synthetic table model of test_sheet_collab."""
from __future__ import annotations

import json
import logging
import sys

import pytest

from synwevo_docs.collab import sheet_live_enabled, sheet_live_report, sheet_live_scope

from .test_sheet_collab import SHEET, edited
from .test_sheet_live import SheetPeer, join, machine_error, machine_put, put, row, world  # noqa: F401 — fixtures


# ------------------------------------------------------------------ reading the switch
@pytest.mark.parametrize("raw", [None, "", "   ", "0", "false", "no", "off", "OFF", " Off ", ",", " , ,, "])
def test_off_in_every_spelling(monkeypatch, raw):
    if raw is None:
        monkeypatch.delenv("SYNWEVO_SHEET_LIVE", raising=False)
    else:
        monkeypatch.setenv("SYNWEVO_SHEET_LIVE", raw)
    assert sheet_live_scope() is False
    assert not sheet_live_enabled("ws_any") and not sheet_live_enabled(None)


@pytest.mark.parametrize("raw", ["1", "true", "yes", "on", "ON", " on ", "True"])
def test_on_covers_every_workspace(monkeypatch, raw):
    monkeypatch.setenv("SYNWEVO_SHEET_LIVE", raw)
    assert sheet_live_scope() is True
    assert sheet_live_enabled("ws_any") and sheet_live_enabled("default")


@pytest.mark.parametrize("raw, listed", [
    ("ws_a", {"ws_a"}),
    (" ws_a , ws_b ", {"ws_a", "ws_b"}),                       # whitespace around the value and each id
    ("ws_a,,ws_b,", {"ws_a", "ws_b"}),                          # empty entries
    (",ws_a", {"ws_a"}),
    ("ws_a,ws_a", {"ws_a"}),
])
def test_a_list_covers_exactly_its_workspaces(monkeypatch, raw, listed):
    monkeypatch.setenv("SYNWEVO_SHEET_LIVE", raw)
    assert sheet_live_scope() == frozenset(listed)
    for wid in listed:
        assert sheet_live_enabled(wid)
    assert not sheet_live_enabled("ws_c") and not sheet_live_enabled(None) and not sheet_live_enabled("")
    assert not sheet_live_enabled("WS_A") and not sheet_live_enabled("ws_ax"), "ids are exact"


def test_a_list_that_also_says_on_is_still_a_list(monkeypatch):
    """A keyword inside a list is not a keyword: "on,ws_a" covers ws_a (and a workspace named "on", of
    which there is none) — never every workspace."""
    monkeypatch.setenv("SYNWEVO_SHEET_LIVE", "on,ws_a")
    assert sheet_live_scope() == frozenset({"on", "ws_a"})
    assert sheet_live_enabled("ws_a") and not sheet_live_enabled("ws_b")


def test_the_report_names_listed_ids_that_are_no_workspace(world, monkeypatch, tmp_path, caplog):
    wid = world["wid"]
    monkeypatch.setenv("SYNWEVO_SHEET_LIVE", f" {wid} , ws_nosuch ,, ")
    assert sheet_live_report(world["store"]) == {"mode": "workspaces", "workspaces": [wid], "unknown": ["ws_nosuch"]}
    monkeypatch.setenv("SYNWEVO_SHEET_LIVE", "on")
    assert sheet_live_report(world["store"])["mode"] == "on"
    monkeypatch.setenv("SYNWEVO_SHEET_LIVE", "off")
    assert sheet_live_report(world["store"]) == {"mode": "off", "workspaces": [], "unknown": []}
    # said once when Docs starts: a mistyped id would otherwise keep the trial off without a word
    from synwevo_docs.api.app import create_app
    monkeypatch.setenv("SYNWEVO_SHEET_LIVE", "ws_typo")
    with caplog.at_level(logging.INFO, logger="synwevo.docs.collab"):
        create_app(str(tmp_path / "other"), start_jobs=False)
    assert any("ws_typo" in r.getMessage() and r.levelno == logging.WARNING for r in caplog.records), caplog.text


# ------------------------------------------------------------------ a trial in one workspace
def _stranger_template(world) -> tuple[str, object]:
    """A table template in the stranger's own workspace, and his session there."""
    cs_ = world["client_for"](world["stranger"], workspace=world["other_wid"])
    t = cs_.post("/api/templates/builtin", json={"kind": "builtin_sheet", "editor": SHEET, "name": "另一个空间的表格"})
    assert t.status_code == 200, t.text
    return t.json()["id"], cs_


def _join_as(world, who, tid, workspace=None) -> SheetPeer:
    return SheetPeer(world["client_for"](world[who], workspace=workspace), tid)


def test_only_the_listed_workspace_s_tables_are_live(world, monkeypatch):
    monkeypatch.setenv("SYNWEVO_SHEET_LIVE", f"  {world['wid']} ,")
    other_tid, cs_ = _stranger_template(world)
    # listed: live
    a = join(world, "editor")
    assert a.hello["live"] is True and a.hello["epoch"]
    assert a.do(put(a, "A2", "试用中"))["ok"]
    r = world["co"].put(f"/api/templates/{world['tid']}/builtin", json={"editor": edited({"C2": {"v": "旧页面"}}), "base_version": 1})
    assert r.status_code == 409 and r.json()["error"] == "live_room", "the listed workspace's live room refuses a whole-table PUT"
    # not listed, in the same process: today's presence room on every path
    b = _join_as(world, "stranger", other_tid, world["other_wid"])
    assert "live" not in b.hello and b.hello["writable"] is False and b.hello["epoch"] is None and "content" not in b.hello
    b.frame({"type": "op", "epoch": "x", "n": 1, "expect": 0, "op": {"op": "set"}})       # ignored: no such thing here
    r = cs_.post(f"/api/templates/{other_tid}/collab/save", json={})
    assert r.status_code == 409 and r.json()["error"] == "not_live", r.text
    r = cs_.put(f"/api/templates/{other_tid}/builtin", json={"editor": edited({"A2": {"v": "照旧"}}), "base_version": 1})
    assert r.status_code == 200 and r.json()["head"] == 2, "the page saves its whole table as before (no live_room)"
    assert b.meta_of("head")["head"] == 2
    assert not b.sheet, "no live table frame was ever sent"
    assert world["app"].state.collab.live_state(other_tid) == {"dirty": False, "online": ["Stranger"], "online_ids": [world["stranger"]["id"]],
                                                                "last_by": None, "live_table": False}
    b.close()
    a.close()
    assert row(world)["dirty"] == 1, "the listed room wrote its draft"
    assert world["store"].one("SELECT 1 AS x FROM collab_docs WHERE room=?", (f"template:{other_tid}",)) is None, "nothing persisted for a presence room"


def test_an_id_that_names_no_workspace_makes_nothing_live(world, monkeypatch):
    monkeypatch.setenv("SYNWEVO_SHEET_LIVE", "ws_nosuch")
    a = join(world, "editor")
    assert "live" not in a.hello and a.hello["epoch"] is None
    r = world["co"].post(f"/api/templates/{world['tid']}/collab/save", json={})
    assert r.status_code == 409 and r.json()["error"] == "not_live"
    a.close()


def test_a_workspace_taken_off_the_list_has_no_live_draft_anywhere(world, monkeypatch):
    """Both workspaces in the trial leave unsaved live edits; then only the first stays listed (a
    restart of Docs with the shorter list). The second's draft is not read on joining, does not stop
    a save in the guard and is not reported as unsaved — it is CI-47's table again, as §9's rollback
    says (which is why its drafts are saved or discarded first) — while the first's still is."""
    wid, owid = world["wid"], world["other_wid"]
    monkeypatch.setenv("SYNWEVO_SHEET_LIVE", f"{wid},{owid}")
    other_tid, cs_ = _stranger_template(world)
    a = join(world, "editor")
    b = _join_as(world, "stranger", other_tid, owid)
    assert a.hello["live"] is True and b.hello["live"] is True
    assert a.do(put(a, "A2", "留在试用里"))["ok"] and b.do(put(b, "A2", "移出试用"))["ok"]
    a.close()
    b.close()
    hub = world["app"].state.collab
    hub.settled(f"template:{other_tid}")
    drafts = {r["template_id"]: r["dirty"] for r in world["store"].all("SELECT template_id, dirty FROM collab_docs WHERE model='sheet'")}
    assert drafts == {world["tid"]: 1, other_tid: 1}
    monkeypatch.setenv("SYNWEVO_SHEET_LIVE", wid)                  # the second workspace leaves the trial
    # the change list and the guard: its draft is not unsaved live work any more; the first's is
    assert cs_.get(f"/api/templates/{other_tid}/collab/changes").json()["dirty"] is False
    assert world["co"].get(f"/api/templates/{world['tid']}/collab/changes").json()["dirty"] is True
    assert hub.live_state(other_tid)["dirty"] is False and hub.live_state(world["tid"])["dirty"] is True
    r = machine_put(world, edited({"A3": {"v": "x"}}), 1)
    assert r.status_code == 409 and machine_error(r)[0] == "live_edits", "the listed workspace's draft still stops an outside save"
    # joining: a presence room that never reads the draft
    c = _join_as(world, "stranger", other_tid, owid)
    assert "live" not in c.hello and "content" not in c.hello and c.hello["dirty"] is False
    c.close()
    # an outside save goes through, and the version change drops the draft (§9: why it is saved or
    # discarded before the workspace leaves the list)
    r = cs_.put(f"/api/templates/{other_tid}/builtin", json={"editor": edited({"A3": {"v": "移出之后"}}), "base_version": 1})
    assert r.status_code == 200, r.text
    hub.settled(f"template:{other_tid}")
    assert world["store"].one("SELECT 1 AS x FROM collab_docs WHERE room=?", (f"template:{other_tid}",)) is None
    assert row(world)["dirty"] == 1, "the listed workspace's draft is untouched"


def test_the_operators_command_lists_unsaved_live_tables_by_workspace(world, monkeypatch, capsys):
    """``python -m synwevo_docs.cli sheet-live``: §9's first rollback step without a database shell."""
    from synwevo_docs import cli
    wid = world["wid"]
    monkeypatch.setenv("SYNWEVO_SHEET_LIVE", f"{wid}, ws_typo")
    a = join(world, "editor")
    assert a.do(put(a, "A2", "未保存"))["ok"]
    a.close()
    assert row(world)["dirty"] == 1
    data = str(world["tmp_path"] / "data")

    def run(*argv) -> str:
        monkeypatch.setattr(sys, "argv", ["synwevo_docs.cli", "--data", data, "sheet-live", *argv])
        assert cli.main() == 0
        return capsys.readouterr().out

    out = json.loads(run("--json"))
    assert out["mode"] == "workspaces" and out["workspaces"] == [wid] and out["unknown"] == ["ws_typo"]
    name = world["store"].one("SELECT name FROM workspaces WHERE id=?", (wid,))["name"]
    assert out["workspace_names"] == {wid: name}, "the operator sees which workspace the id is"
    assert [(d["workspace"], d["template_id"], d["head"], d["last_editor"], d["last_editor_name"]) for d in out["unsaved_drafts"]] == \
        [(wid, world["tid"], 1, world["editor"]["id"], "Editor")]
    assert json.loads(run("--json", "--workspace", world["other_wid"]))["unsaved_drafts"] == []
    text = run()
    assert f"试用：{wid}（{name}）" in text and "ws_typo" in text and world["tid"] in text and "未保存的表格  : 1" in text
    # saved: nothing left to list
    r = world["co"].post(f"/api/templates/{world['tid']}/collab/save", json={})
    assert r.status_code == 409, "nobody in the room: saving it needs the room open"
    b = join(world, "owner")
    assert world["co"].post(f"/api/templates/{world['tid']}/collab/save", json={}).status_code == 200
    b.close()
    world["app"].state.collab.settled(f"template:{world['tid']}")
    assert json.loads(run("--json"))["unsaved_drafts"] == []
