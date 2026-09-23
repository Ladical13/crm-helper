"""The offline outbox: a door saved in a dead zone is not a door lost.

Until this existed, a pin POST that failed was shown in an `alert()` and
dropped. This app is built for driveways on one bar of signal, so that threw
away the one write it exists to capture, at exactly the moment it exists for.

The queue is the easy half. The half these tests mostly cover is the other
one: a retry must never write the same door twice. A lost pin is a door nobody
recorded; a duplicated pin is a door two reps each believe the other knocked,
and it inflates the leaderboard they are paid on.
"""
import os
import re
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
sys.path.insert(0, os.path.dirname(os.path.dirname(HERE)))

APP_JS = open(os.path.join(HERE, '..', 'static', 'app.js'), encoding='utf-8').read()


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv('CANVASSER_DATA_DIR', str(tmp_path))
    monkeypatch.setenv('PORTAL_DATA_DIR', str(tmp_path))
    monkeypatch.setenv('SESSION_SECRET', 'test-secret')
    for mod in [m for m in list(sys.modules) if m in ('app', 'p1_canvass_app')]:
        del sys.modules[mod]
    import app as canvasser_app
    canvasser_app.app.config['TESTING'] = True
    from portal import users as pusers
    for username in ('aaron', 'bryan'):
        if not pusers.get(username):
            pusers.create(username, password='test-only', role='rep')
    with canvasser_app.app.test_client() as c:
        with c.session_transaction() as sess:
            sess['username'] = 'aaron'
            sess['is_admin'] = False
        yield c, canvasser_app


def _pin(**kw):
    body = {'lat': 40.585, 'lng': -105.084, 'pin_type': 'interested',
            'address': '1420 Oak St', 'contact_name': 'Jennifer'}
    body.update(kw)
    return body


def _count(mod):
    with mod.get_db() as db:
        return db.execute('SELECT COUNT(*) AS n FROM pins').fetchone()['n']


# ── The retry must not write a second door ──────────────────────────────────

def test_a_replayed_save_returns_the_first_pin_rather_than_a_second_door(client):
    """The failure the outbox would otherwise introduce.

    The common case is not a save that failed — it is a save that SUCCEEDED and
    whose response never made it back down a bar of signal. The queue cannot
    tell those apart, so it retries, and without a dedupe key the retry writes
    the same house again under a new id.
    """
    c, mod = client
    first = c.post('/api/pins', json=_pin(client_id='abc-123'))
    assert first.status_code == 201

    again = c.post('/api/pins', json=_pin(client_id='abc-123'))
    assert again.status_code == 200, 'a replay must not report a fresh create'
    assert again.get_json()['id'] == first.get_json()['id']
    assert _count(mod) == 1


def test_a_replay_returns_the_row_as_it_stands_not_as_it_was_sent(client):
    """A queued pin can land, be edited at the door, and only then have its
    duplicate arrive. The replay is a no-op, so it hands back what is stored —
    re-applying the original payload would silently undo the rep's edit."""
    c, mod = client
    created = c.post('/api/pins', json=_pin(client_id='dup-1')).get_json()
    c.put(f"/api/pins/{created['id']}", json={'notes': 'dog in the yard'})

    replay = c.post('/api/pins', json=_pin(client_id='dup-1')).get_json()
    assert replay['notes'] == 'dog in the yard'


def test_two_pins_without_a_client_id_do_not_collide(client):
    """Every pin written before this existed carries an empty client_id, and a
    plain unique index would make the whole table one row. The index is partial
    for that reason, and this is what holds it partial."""
    c, mod = client
    assert c.post('/api/pins', json=_pin()).status_code == 201
    assert c.post('/api/pins', json=_pin(address='9 Elm St')).status_code == 201
    assert _count(mod) == 2


def test_two_reps_may_use_the_same_client_id(client):
    """The id comes from a browser, so it is only unique per rep by
    construction. Scoping the key to the rep means one rep's replay can never
    hand back another rep's door — which would also leak a contact name."""
    c, mod = client
    c.post('/api/pins', json=_pin(client_id='same'))
    with c.session_transaction() as sess:
        sess['username'] = 'bryan'
    r = c.post('/api/pins', json=_pin(client_id='same', address='9 Elm St'))
    assert r.status_code == 201
    assert _count(mod) == 2


def test_the_stored_pin_keeps_its_client_id(client):
    c, _ = client
    assert c.post('/api/pins', json=_pin(client_id='keep-me')).get_json()['client_id'] == 'keep-me'


def test_a_pin_the_server_refuses_is_refused_not_absorbed(client):
    """A bad pin type will never succeed however many times it is retried. It
    has to come back 4xx so the queue drops it and tells the rep, rather than
    sitting in an outbox that never drains and that nobody can see."""
    c, _ = client
    r = c.post('/api/pins', json=_pin(client_id='bad', pin_type='not_a_type'))
    assert r.status_code == 400


def test_an_absurd_client_id_cannot_blow_up_the_row(client):
    c, _ = client
    r = c.post('/api/pins', json=_pin(client_id='x' * 5000))
    assert r.status_code == 201
    assert len(r.get_json()['client_id']) <= 64


# ── The front-end contract ──────────────────────────────────────────────────
#
# Static assertions, in the style of test_assets.py. They cannot prove the
# queue drains, but each one pins a decision that would otherwise be silently
# reverted by someone who did not know why it was made.

def test_a_failed_save_is_queued_rather_than_alerted_away():
    assert 'savePinThroughOutbox' in APP_JS
    save = APP_JS[APP_JS.index("$('save-pin-btn')"):]
    save = save[:save.index('async function getGPS')]
    assert 'savePinThroughOutbox' in save, 'the save button bypasses the outbox'


def test_the_queue_is_indexeddb_so_it_survives_the_app_being_killed():
    """iOS reclaiming a backgrounded tab is the normal end of a canvassing
    session, not an edge case. A queue in a module-level array is gone."""
    assert 'indexedDB.open' in APP_JS
    assert re.search(r"OUTBOX_STORE\s*=\s*'outbox'", APP_JS)


def test_every_queued_save_carries_a_client_id():
    assert re.search(r'payload\.client_id\s*=\s*payload\.client_id\s*\|\|\s*newClientId\(\)',
                     APP_JS), 'a queued pin with no client_id duplicates on retry'


def test_the_outbox_is_keyed_on_the_client_id():
    """Keyed on anything else and one door queued twice becomes two doors even
    before the network is involved."""
    assert re.search(r"createObjectStore\(OUTBOX_STORE,\s*\{\s*keyPath:\s*'client_id'", APP_JS)


def test_the_flush_is_driven_by_the_page_not_by_background_sync():
    """Background Sync is the textbook answer and it is the wrong one here:
    WebKit has never shipped it, and every rep on this team runs this as an
    installed PWA on an iPhone. A `sync` handler would be dead code on the
    devices the feature exists for while reading as though it were handled.
    """
    assert "addEventListener('online'" in APP_JS
    assert "visibilitychange" in APP_JS
    assert '.sync.register' not in APP_JS
    assert 'SyncManager' not in APP_JS


def test_a_background_flush_does_not_bounce_the_rep_to_login():
    """api() redirects on 401, which is right for a tap someone is watching and
    wrong for a flush: it would throw a rep out of the app mid-street."""
    flush = APP_JS[APP_JS.index('async function flushOutbox'):]
    flush = flush[:flush.index('function replacePendingPin')]
    assert 'postPinRaw' in flush
    assert "await api(" not in flush
    assert 'break' in flush, '401 during a flush must stop it, not drop the queue'


def test_a_permanently_refused_pin_leaves_the_queue():
    flush = APP_JS[APP_JS.index('async function flushOutbox'):]
    flush = flush[:flush.index('function replacePendingPin')]
    assert re.search(r'out\.status\s*<\s*500', flush), \
        'a 4xx must drop out of the queue, or the outbox never drains'


def test_a_queued_pin_is_still_drawn_for_the_rep():
    """The rep's own work has to be visible on their own map, or the feature
    reads as data loss even when nothing was lost."""
    assert 'pendingPinFrom' in APP_JS
    assert 'restorePendingPins' in APP_JS
    assert re.search(r"pin\.pending\s*\?\s*' pending'", APP_JS)


def test_a_queued_pin_does_not_open_a_detail_panel_it_cannot_serve():
    """It has no server id, so edit, delete and Add to Pipeline would all
    address a row that does not exist yet."""
    marker = APP_JS[APP_JS.index('function buildPinMarker'):]
    marker = marker[:marker.index('function addPinMarker')]
    assert 'if (pin.pending)' in marker
    assert marker.index('if (pin.pending)') < marker.index('showPinDetail(pin)')


def test_storage_being_unavailable_does_not_break_dropping_a_pin():
    """Private browsing, blocked site data and a quota refusal all throw. A rep
    losing the queue is bad; a rep unable to drop a pin at all is worse."""
    for fn in ('outboxAll', 'outboxPut', 'outboxDelete'):
        m = re.search(fn + r'\s*=\s*\S.*?catch\(', APP_JS, re.S)
        assert m, f'{fn} does not degrade when IndexedDB is unavailable'
