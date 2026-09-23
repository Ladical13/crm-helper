from test_behaviour import client
from portal import users
import subprocess
from pathlib import Path


def test_deleted_account_cannot_keep_using_pin_api(client):
    c, _ = client
    with users.get_db() as db:
        db.execute("DELETE FROM users WHERE username='aaron'")
    assert c.get('/api/pins').status_code == 401


def test_stale_admin_cookie_cannot_edit_another_reps_pin(client):
    c, _ = client
    with c.session_transaction() as session:
        session['username'] = 'bryan'
    p = c.post('/api/pins', json={'lat':40.5, 'lng':-105}).get_json()
    with c.session_transaction() as session:
        session['username'] = 'aaron'
        session['is_admin'] = True
    assert c.put('/api/pins/' + p['id'], json={'notes':'unauthorized'}).status_code == 403
    assert c.delete('/api/pins/' + p['id']).status_code == 403


def test_outbox_owner_cannot_change_with_browser_account(client):
    c, _ = client
    assert c.post('/api/pins', json={'owner':'bryan','lat':40.5,'lng':-105}).status_code == 403


def test_invalid_coordinates_never_poison_the_map(client):
    c, _ = client
    for lat in ('oops', 100, float('inf')):
        assert c.post('/api/pins', json={'lat':lat, 'lng':-105}).status_code == 400


def test_browser_failure_paths_and_cache_isolation():
    subprocess.run(['node', str(Path(__file__).with_name('reliability_runner.js'))], check=True)
