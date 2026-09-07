"""The service worker's outbox, exercised as the browser would run it.

`outbox_runner.js` loads static/sw.js itself rather than restating it — the
same approach as the estimator's parity runner — so renaming a function in the
worker fails here instead of passing while the shipped code does something
else. It stands in a fake IndexedDB, fake caches and a scriptable fetch.
"""
import json
import os
import subprocess

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
RUNNER = os.path.join(HERE, 'outbox_runner.js')


@pytest.fixture(scope='module')
def outbox():
    try:
        proc = subprocess.run(['node', RUNNER], capture_output=True, text=True)
    except FileNotFoundError:
        pytest.skip('node not installed')
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout)


def test_a_write_with_no_network_is_kept(outbox):
    """The bug: every mutation was dropped on the floor the moment the signal
    went. A rep logging a door knock in a driveway lost it, with a red toast as
    the only trace."""
    assert outbox['queued_rows'] == 1


def test_the_page_is_told_it_is_only_on_the_phone(outbox):
    """202 Accepted, not a fake 200. Pretending the write landed is how a rep
    finds out on Monday that Thursday never happened."""
    assert outbox['queued_status'] == 202
    assert outbox['queued_body'] == {'queued': True}


def test_the_idempotency_key_survives_the_queue(outbox):
    """It is the whole reason replay is safe: the retry has to be
    indistinguishable from the original request."""
    assert outbox['queued_keeps_key']


def test_someone_elses_api_is_not_queued(outbox):
    """A cross-origin POST is none of our business, and a queued one would be
    replayed at somebody else's server."""
    assert outbox['cross_origin_rows'] == 0
    assert outbox['cross_origin_untouched']


def test_the_queue_drains_in_the_order_it_was_made(outbox):
    """A stage move then a note is not the same story as a note then a stage
    move, and a human reads the timeline."""
    assert outbox['before_drain'] == 3
    assert outbox['drain_order'] == [1, 2, 3]
    assert outbox['after_drain'] == 0


def test_the_page_is_told_when_the_queue_drains(outbox):
    """So it can re-render: what drained was written against a stale view."""
    assert outbox['notified'] == [{'type': 'outbox-drained', 'count': 3}]


def test_a_rejected_write_is_not_retried_forever(outbox):
    """A 4xx is an answer. Keeping it would retry a write the server will never
    accept, on every reconnect, for the life of the install."""
    assert outbox['rejected_dropped'] == 0


def test_a_server_stumble_is_retried(outbox):
    """A 5xx is not an answer — it is the server having a bad moment, and the
    rep's work must outlast it."""
    assert outbox['server_error_kept'] == 1
