"""API surface: auth guard, estimate CRUD, and the customer-facing sign page."""
import json
import os

import pytest

from conftest import TEST_DATA_DIR


# ── auth guard (default-deny) ──────────────────────────────────────────

def test_api_requires_login(anon):
    assert anon.get('/api/estimates').status_code == 401


def test_pages_redirect_to_login(anon):
    r = anon.get('/')
    assert r.status_code == 302
    assert '/login' in r.headers['Location']


def test_login_page_is_public(anon):
    assert anon.get('/login').status_code == 200


def test_logged_in_user_reaches_api(client):
    assert client.get('/api/me').status_code == 200


# ── estimate lifecycle ─────────────────────────────────────────────────

def test_create_read_update_delete(client):
    r = client.post('/api/estimates', json={})
    assert r.status_code == 201
    eid = r.get_json()['estimate_id']

    assert client.get(f'/api/estimates/{eid}').status_code == 200

    doc = client.get(f'/api/estimates/{eid}').get_json()
    doc['customer'] = {'name': 'Ada Lovelace', 'address': {'city': 'Tyler'}}
    assert client.put(f'/api/estimates/{eid}', json=doc).status_code == 200
    assert client.get(f'/api/estimates/{eid}').get_json()['customer']['name'] == 'Ada Lovelace'

    assert client.delete(f'/api/estimates/{eid}').status_code == 200
    assert client.get(f'/api/estimates/{eid}').status_code == 404


def test_new_estimate_appears_in_list(client):
    eid = client.post('/api/estimates', json={}).get_json()['estimate_id']
    try:
        ids = [e['estimate_id'] for e in client.get('/api/estimates').get_json()]
        assert eid in ids
    finally:
        client.delete(f'/api/estimates/{eid}')


def test_duplicate_creates_a_distinct_estimate(client):
    eid = client.post('/api/estimates', json={}).get_json()['estimate_id']
    r = client.post(f'/api/estimates/{eid}/duplicate')
    assert r.status_code in (200, 201)
    dup = r.get_json()['estimate_id']
    try:
        assert dup != eid
        assert client.get(f'/api/estimates/{dup}').status_code == 200
    finally:
        client.delete(f'/api/estimates/{eid}')
        client.delete(f'/api/estimates/{dup}')


# ── path traversal ─────────────────────────────────────────────────────

@pytest.mark.parametrize('bad', ['../../etc/passwd', '', '..', 'a/../../b'])
def test_safe_path_id_rejects_traversal(A, bad):
    assert not A._safe_path_id(bad)


def test_safe_path_id_accepts_real_ids(A):
    assert A._safe_path_id('9e42-45d7-b052-mq5p065g')


def test_traversal_id_is_not_served(client):
    assert client.get('/api/estimates/..%2f..%2fapp').status_code != 200


# ── malformed estimates must not disappear ─────────────────────────────

def _write_raw(name, payload):
    p = os.path.join(TEST_DATA_DIR, 'estimates', f'{name}.json')
    with open(p, 'w', encoding='utf-8') as f:
        json.dump(payload, f)
    return p


def test_malformed_estimate_is_surfaced_not_silently_dropped(client):
    """A rep must never watch a job vanish from the dashboard with no trace."""
    p = _write_raw('broken-doc', {
        'estimate_id': 'broken-doc',
        'customer': 'a string where a dict belongs',  # blows up the summarizer
        'status': 'sent',
    })
    try:
        rows = client.get('/api/estimates').get_json()
        row = next((r for r in rows if r['estimate_id'] == 'broken-doc'), None)
        assert row is not None, 'malformed estimate was silently dropped from the list'
        assert 'load_error' in row
    finally:
        os.remove(p)


def _rep_client(app, name='aaron'):
    c = app.test_client()
    with c.session_transaction() as s:
        s['user'] = name  # not in users.json -> resolves to 'rep'
    return c


def test_rep_does_not_see_another_reps_estimate(app):
    """Positive control: the ownership filter is actually on for this user."""
    p = _write_raw('owned-by-someone-else', {
        'estimate_id': 'owned-by-someone-else',
        'salesperson': 'someone.else',
        'customer': {'name': 'Not Yours'},
    })
    try:
        rows = _rep_client(app).get('/api/estimates').get_json()
        assert not any(r['estimate_id'] == 'owned-by-someone-else' for r in rows)
    finally:
        os.remove(p)


def test_unreadable_salesperson_fails_closed_for_reps(app):
    """A salesperson field that isn't a string must not raise (500ing the whole
    dashboard) nor leak the estimate to a rep who doesn't own it."""
    p = _write_raw('weird-salesperson', {
        'estimate_id': 'weird-salesperson',
        'salesperson': 12345,               # breaks a naive .strip()
        'customer': {'name': 'Mystery'},
    })
    try:
        r = _rep_client(app).get('/api/estimates')
        assert r.status_code == 200, 'an unreadable salesperson took down the list'
        assert not any(x['estimate_id'] == 'weird-salesperson' for x in r.get_json()), \
            'estimate with an unreadable owner leaked to a rep'
    finally:
        os.remove(p)


def test_manager_still_sees_estimate_with_unreadable_salesperson(client):
    """Failing closed applies to reps; a manager/admin must still see the doc
    so the bad data is discoverable rather than invisible to everyone."""
    p = _write_raw('weird-salesperson-mgr', {
        'estimate_id': 'weird-salesperson-mgr',
        'salesperson': 12345,
        'customer': {'name': 'Mystery'},
    })
    try:
        rows = client.get('/api/estimates').get_json()  # client == luke == admin
        assert any(r['estimate_id'] == 'weird-salesperson-mgr' for r in rows)
    finally:
        os.remove(p)


def test_non_object_estimate_document_is_skipped(client):
    """A JSON file holding a list/string must not 500 the whole dashboard."""
    p = _write_raw('not-an-object', ['this', 'is', 'a', 'list'])
    try:
        assert client.get('/api/estimates').status_code == 200
    finally:
        os.remove(p)
