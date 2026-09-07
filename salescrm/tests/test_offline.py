"""A write made with no signal is not a write that did not happen.

Every mutation used to be dropped the moment the network went — the door knock
a rep logged in a driveway simply vanished, with a red toast as the only trace.
The canvasser has had an offline shell for a year; the CRM, which is where the
knock is actually recorded, had none.

Replay is what makes queueing safe, and replay is what these pin: the phone
gives up on a request whose RESPONSE was lost, so the row is already written
when the retry arrives.
"""
from conftest import signup, new_lead
import app as appmod


def _post(client, path, body, key):
    return client.post(path, json=body, headers={'Idempotency-Key': key})


# ── Activities: the door knock ───────────────────────────────────────────────

def test_a_replayed_knock_is_logged_once(client):
    """Twice on the timeline and twice on the leaderboard, otherwise."""
    signup(client)
    lead = new_lead(client)
    for _ in range(3):
        _post(client, f'/api/leads/{lead["id"]}/activities', {'kind': 'door'}, 'k1')
    with appmod.get_db() as db:
        n = db.execute("SELECT COUNT(*) c FROM activities WHERE lead_id=? AND kind='door'",
                       (lead['id'],)).fetchone()['c']
    assert n == 1
    assert client.get('/api/leaderboard').get_json()[0]['outreach'] == 1


def test_a_replay_answers_what_the_original_answered(client):
    """The retry must be indistinguishable from the first success — the page
    cannot tell them apart and should not have to."""
    signup(client)
    lead = new_lead(client)
    first = _post(client, f'/api/leads/{lead["id"]}/activities', {'kind': 'call'}, 'k2')
    again = _post(client, f'/api/leads/{lead["id"]}/activities', {'kind': 'call'}, 'k2')
    assert again.status_code == first.status_code == 201
    assert again.get_json() == first.get_json()


def test_different_keys_are_different_calls(client):
    """Two real calls to the same person are two calls."""
    signup(client)
    lead = new_lead(client)
    _post(client, f'/api/leads/{lead["id"]}/activities', {'kind': 'call'}, 'a')
    _post(client, f'/api/leads/{lead["id"]}/activities', {'kind': 'call'}, 'b')
    assert client.get('/api/leaderboard').get_json()[0]['outreach'] == 2


def test_no_key_still_works(client):
    """Every existing caller, and any client that never queues, is unaffected."""
    signup(client)
    lead = new_lead(client)
    r = client.post(f'/api/leads/{lead["id"]}/activities', json={'kind': 'call'})
    assert r.status_code == 201


# ── Leads: the lead created at the door ──────────────────────────────────────

def test_a_replayed_create_returns_the_original_lead(client):
    signup(client)
    body = {'first_name': 'Dana', 'lead_type': 'homeowner'}
    first = _post(client, '/api/leads', body, 'newlead')
    again = _post(client, '/api/leads', body, 'newlead')
    assert again.get_json()['id'] == first.get_json()['id']
    with appmod.get_db() as db:
        assert db.execute('SELECT COUNT(*) c FROM leads').fetchone()['c'] == 1


def test_the_cross_sell_second_deal_still_works(client):
    """POST /api/leads is duplicate-friendly on purpose: the Pitch button makes
    a second deal for the same person. The replay guard is the KEY, never the
    contact details — matching on those would break it."""
    signup(client)
    body = {'first_name': 'Dana', 'phone': '9705551212', 'email': 'd@example.com'}
    a = _post(client, '/api/leads', dict(body, service='roofing'), 'p1')
    b = _post(client, '/api/leads', dict(body, service='window_cleaning'), 'p2')
    assert a.get_json()['id'] != b.get_json()['id']


# ── Housekeeping ─────────────────────────────────────────────────────────────

def test_keys_older_than_the_window_are_pruned(client):
    """A queue that never drains is a bug elsewhere; a key older than the
    window cannot still be in flight, and the table would grow forever."""
    signup(client)
    lead = new_lead(client)
    _post(client, f'/api/leads/{lead["id"]}/activities', {'kind': 'call'}, 'old')
    with appmod.get_db() as db:
        db.execute("UPDATE idempotency SET created_at='2020-01-01T00:00:00Z'")
    _post(client, f'/api/leads/{lead["id"]}/activities', {'kind': 'call'}, 'fresh')
    with appmod.get_db() as db:
        keys = [r['key'] for r in db.execute('SELECT key FROM idempotency')]
    assert keys == ['fresh']


def test_a_key_matches_until_it_is_actually_pruned(client):
    """Age alone does not stop a key matching, and that is deliberate: matching
    for longer than strictly necessary only ever suppresses a duplicate, while
    expiring eagerly risks writing one. The sweep is what ends a key's life."""
    signup(client)
    lead = new_lead(client)
    _post(client, f'/api/leads/{lead["id"]}/activities', {'kind': 'call'}, 'x')
    with appmod.get_db() as db:
        db.execute("UPDATE idempotency SET created_at='2020-01-01T00:00:00Z'")
    _post(client, f'/api/leads/{lead["id"]}/activities', {'kind': 'call'}, 'x')
    assert client.get('/api/leaderboard').get_json()[0]['outreach'] == 1

    # ...and once a later write has swept it, the key is free again.
    _post(client, f'/api/leads/{lead["id"]}/activities', {'kind': 'call'}, 'sweeper')
    _post(client, f'/api/leads/{lead["id"]}/activities', {'kind': 'call'}, 'x')
    assert client.get('/api/leaderboard').get_json()[0]['outreach'] == 3
