"""The explicit scanner double releases listening and partially-read sockets."""
from __future__ import annotations

import gc
import socket
import time
import weakref

from .clamd_double import ClamdDouble


def _wake_original_accept(double):
    # Diagnostic cleanup lets this regression fail safely against the original close-only code.
    if double.thread.is_alive():
        try:
            with socket.create_connection(("127.0.0.1", double.port), timeout=0.5):
                pass
        except OSError:
            pass
        double.thread.join(1.0)


def test_closed_doubles_release_accept_threads_and_instances():
    refs = []
    for _ in range(6):
        double = ClamdDouble()
        try:
            with socket.create_connection(("127.0.0.1", double.port), timeout=1.0) as client:
                client.sendall(b"zPING\0")
                assert client.recv(64) == b"PONG\0"
            refs.append(weakref.ref(double))
            double.close()
            double.thread.join(0.2)
            assert not double.thread.is_alive(), "close left the scanner fixture blocked in accept()"
            double.close()  # teardown remains idempotent
        finally:
            double.close()
            _wake_original_accept(double)
        del double
    gc.collect()
    assert all(ref() is None for ref in refs)


def test_close_releases_a_handler_waiting_for_the_rest_of_a_frame():
    double = ClamdDouble()
    try:
        with socket.create_connection(("127.0.0.1", double.port), timeout=1.0) as client:
            client.sendall(b"zPING\0")
            assert client.recv(64) == b"PONG\0"
        deadline = time.monotonic() + 1.0
        while double._handlers and time.monotonic() < deadline:
            time.sleep(0.005)
        assert not double._handlers, "completed PING handler must finish before the partial frame"
        client = socket.create_connection(("127.0.0.1", double.port), timeout=1.0)
        try:
            client.sendall(b"zINSTREAM\0\x00")  # incomplete chunk header; handler must be interruptible
            deadline = time.monotonic() + 1.0
            while not double._handlers and time.monotonic() < deadline:
                time.sleep(0.005)
            handlers = tuple(double._handlers)
            assert handlers, "fixture must accept the partial frame before teardown"
            double.close()
            assert not double.thread.is_alive()
            assert all(not thread.is_alive() for thread in handlers)
            assert double._connections == set() and double._handlers == set()
        finally:
            client.close()
    finally:
        double.close()
        _wake_original_accept(double)
