"""Deterministic timer fault injection: shutdown must leave retries for restart."""
import threading

import pytest

from synwevo_core.store import Store
from synwevo_docs.services import App


class TimerFixture:
    """A cancelled timer may already have entered its callback in a real process."""
    def __init__(self, delay, function):
        self.function = function
        self.cancelled = False

    def start(self):
        pass

    def cancel(self):
        self.cancelled = True


@pytest.fixture
def app(tmp_path, monkeypatch):
    instance = App(Store(tmp_path / "data"))
    monkeypatch.setattr("synwevo_docs.services.threading.Timer", TimerFixture)
    yield instance
    instance.close()


def test_late_scan_timer_and_running_scanner_cannot_enqueue_after_shutdown(app):
    app._schedule_scan_retry("fixture-source", 10)
    timer = app._scan_timers["fixture-source"]
    app.close()
    assert timer.cancelled
    # Reproduce the exact callback which previously raised from executor.submit().
    timer.function()
    app._schedule_scan_retry("fixture-source", 10)
    assert not app._scan_timers
    app.close()  # repeated shutdown remains safe


def test_replaced_timer_does_not_erase_or_run_the_new_retry(app, monkeypatch):
    seen = []
    finished = threading.Event()
    monkeypatch.setattr(app, "_scan_then_parse", lambda sid: (seen.append(sid), finished.set()))
    app._schedule_scan_retry("fixture-source", 10)
    old = app._scan_timers["fixture-source"]
    app._schedule_scan_retry("fixture-source", 20)
    current = app._scan_timers["fixture-source"]
    assert old.cancelled
    old.function()
    assert not seen and app._scan_timers["fixture-source"] is current
    current.function()
    assert finished.wait(2)
    assert seen == ["fixture-source"] and not app._scan_timers


def test_timer_submission_and_close_are_serialized(app, monkeypatch):
    entered, release, closed = threading.Event(), threading.Event(), threading.Event()
    submit = app.parse_pool.submit
    errors = []

    def paused_submit(*args):
        entered.set()
        assert release.wait(2)
        return submit(*args)

    def invoke(fn):
        try:
            fn()
        except Exception as exc:
            errors.append(exc)

    monkeypatch.setattr(app.parse_pool, "submit", paused_submit)
    monkeypatch.setattr(app, "_scan_then_parse", lambda sid: None)
    app._schedule_scan_retry("fixture-source", 10)
    timer = app._scan_timers["fixture-source"]
    firing = threading.Thread(target=lambda: invoke(timer.function))
    stopping = threading.Thread(target=lambda: (invoke(app.close), closed.set()))
    firing.start()
    assert entered.wait(2)
    stopping.start()
    assert not closed.wait(.05)
    release.set()
    firing.join(2)
    stopping.join(2)
    assert not firing.is_alive() and not stopping.is_alive()
    assert not errors
