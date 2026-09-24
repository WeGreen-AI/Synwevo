"""GET /api/v1/events: a workspace's job events behind an opaque cursor — every event exactly
once, in log order, never another workspace's, with the same id a webhook carries."""
import base64

from .test_webhook_support import fixture_event, hub, sub_rows  # noqa: F401  (hub is a fixture)


def _all(hub, key, **params):
    items, cursor = [], None
    for _ in range(100):
        q = dict(params, **({"cursor": cursor} if cursor else {}))
        r = hub.c.get("/api/v1/events", params=q, headers=hub.keys[key])
        assert r.status_code == 200, r.text
        page = r.json()
        items += page["items"]
        cursor = page["next_cursor"]
        if not page["has_more"]:
            return items, cursor
    raise AssertionError("feed did not end")


def test_feed_returns_each_of_my_events_once_in_order(hub):
    mine = []
    j1, j2 = "j_feed_one", "j_feed_two"
    for i in range(5):
        mine.append(fixture_event(hub.store, "default", "job.created", job_id=j1 if i % 2 else j2)["id"])
        fixture_event(hub.store, "tenant-b", "job.created")                      # interleaved, not mine
    mine.append(fixture_event(hub.store, "default", "job.completed", job_id=j1)["id"])
    items, cursor = _all(hub, "ro", limit=2)
    assert [x["id"] for x in items] == mine                                       # exactly once, log order
    assert {x["job_id"] for x in items} == {j1, j2} and all(x["project_id"] == "p_fixture" for x in items)
    assert set(items[0]) == {"id", "type", "version", "occurred_at", "job_id", "project_id", "data"}
    # Nothing new: the returned cursor stays usable and yields nothing.
    page = hub.c.get("/api/v1/events", params={"cursor": cursor}, headers=hub.keys["ro"]).json()
    assert page == {"items": [], "next_cursor": cursor, "has_more": False}
    later = fixture_event(hub.store, "default", "job.failed")
    page = hub.c.get("/api/v1/events", params={"cursor": cursor}, headers=hub.keys["ro"]).json()
    assert [x["id"] for x in page["items"]] == [later["id"]]


def test_another_workspace_sees_none_of_them(hub):
    for _ in range(3):
        fixture_event(hub.store, "default")
    theirs = fixture_event(hub.store, "tenant-b", "job.failed")
    items, _ = _all(hub, "b_ro")
    assert [x["id"] for x in items] == [theirs["id"]]


def test_type_filter_and_limits(hub):
    fixture_event(hub.store, "default", "job.created")
    done = fixture_event(hub.store, "default", "job.completed")
    failed = fixture_event(hub.store, "default", "job.failed")
    items, _ = _all(hub, "ro", types="job.completed,job.failed")
    assert [x["id"] for x in items] == [done["id"], failed["id"]]
    h = hub.keys["ro"]
    r = hub.c.get("/api/v1/events", params={"types": "job.complete"}, headers=h)
    assert r.status_code == 400 and r.json()["error"]["field"] == "types"
    for bad in (0, 501):
        r = hub.c.get("/api/v1/events", params={"limit": bad}, headers=h)
        assert r.status_code == 400 and r.json()["error"]["field"] == "limit"
    assert hub.c.get("/api/v1/events", params={"limit": 500}, headers=h).status_code == 200
    r = hub.c.get("/api/v1/events", params={"cursor": "%%%"}, headers=h)
    assert r.status_code == 400 and r.json()["error"]["code"] == "invalid_cursor"
    forged = base64.urlsafe_b64encode(b"d1:5").decode().rstrip("=")                # a delivery-log cursor
    assert hub.c.get("/api/v1/events", params={"cursor": forged}, headers=h).status_code == 400
    assert hub.c.get("/api/v1/events").status_code == 401


def test_a_cursor_from_before_a_restore_is_refused(hub):
    fixture_event(hub.store, "default")
    _, cursor = _all(hub, "ro")
    # The database goes back to an older backup: the log is now shorter than the cursor.
    hub.store.execute("DELETE FROM job_events")
    r = hub.c.get("/api/v1/events", params={"cursor": cursor}, headers=hub.keys["ro"])
    assert r.status_code == 410 and r.json()["error"]["code"] == "cursor_expired"


def test_feed_ids_are_the_webhook_ids(hub):
    rx = hub.receiver()
    sub = hub.create(rx.url, events=["*"])
    d = hub.dispatcher()
    fixture_event(hub.store, "default", "job.created")
    fixture_event(hub.store, "default", "job.completed")
    d.run_once()
    items, _ = _all(hub, "ro")
    assert [x["id"] for x in items] == [r["headers"]["webhook-id"] for r in rx.received]
    assert [x["id"] for x in items] == [r["event_id"] for r in sub_rows(hub.store, sub["id"])]


def test_planned_filter_and_exception_redaction(hub):
    event = fixture_event(hub.store, "default", "job.planned", data={"phase": "generating"})
    items, _ = _all(hub, "ro", types="job.planned")
    assert [item["id"] for item in items] == [event["id"]]
    assert items[0]["data"] == {"phase": "generating"}
    fixture_event(hub.store, "default", "job.failed",
                  data={"error": "fixture customer cell", "extra": [{"token": "fixture-token", "ok": 1}]})
    items, _ = _all(hub, "ro", types="job.failed")
    assert items[0]["data"] == {"error_code": "job_error", "extra": [{"ok": 1}]}
