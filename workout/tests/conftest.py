"""Test harness: point the app at a throwaway DB, never at a real training log."""
import os
import sys
import tempfile

# Set BEFORE importing app — DB_PATH is resolved at import time.
_TMP = tempfile.mkdtemp(prefix='workout_test_')
os.environ['WORKOUT_DATA_DIR'] = _TMP
# Accounts live in the portal's shared store; this app owns no identity.
os.environ.setdefault('PORTAL_DATA_DIR', _TMP)
# The standalone-dev identity must not be in play — these tests sign in through
# the real portal session, which is what production does.
os.environ.pop('WORKOUT_DEV_USER', None)
os.environ.pop('RAILWAY_ENVIRONMENT', None)

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import app as appmod  # noqa: E402

import pytest  # noqa: E402

from portal import session as psession  # noqa: E402
from portal import users as pusers      # noqa: E402

# Every table the app writes. The temp DB is created once per session, not per
# test, so a table missing here leaks state between tests — the same trap
# salescrm/tests/conftest.py documents.
TABLES = ['sets', 'workouts', 'routine_items', 'routines', 'exercise_hidden',
          'settings']


def _wipe():
    with appmod.get_db() as db:
        for t in TABLES:
            db.execute(f'DELETE FROM {t}')
        # Custom movements go too, but the seeded library stays: a fresh library
        # on every test is what init_db() would give a new install anyway.
        db.execute('DELETE FROM exercises WHERE is_custom=1')
    # Identity lives in the portal store, so it has to be reset here too or the
    # "first user bootstraps as admin" rule leaks across tests.
    with pusers.get_db() as db:
        db.execute('DELETE FROM users')
        db.execute('DELETE FROM invites')


@pytest.fixture
def app():
    return appmod


@pytest.fixture
def anon(app):
    """A client with no session — nobody is signed in."""
    _wipe()
    app.app.config['TESTING'] = True
    return app.app.test_client()


@pytest.fixture
def client(anon):
    return sign_in(anon, 'luke')


def sign_in(client, username):
    """Create the portal account if needed and put its session on the client.

    Exactly what portal/app.py's login does — this app reads that session and
    owns no login of its own.
    """
    if not pusers.get(username):
        role = 'admin' if pusers.count() == 0 else 'rep'
        pusers.create(username, password='secret1', role=role,
                      full_name=username.title())
    with client.session_transaction() as s:
        psession.sign_in(s, pusers.get(username))
    return client


def second_client(app, username='dalton'):
    """A separate signed-in client, for the isolation tests."""
    return sign_in(app.app.test_client(), username)


@pytest.fixture
def ex_id(client):
    """The id of a known seeded movement."""
    rows = client.get('/api/exercises?q=Back Squat').get_json()
    return rows[0]['id']


def start(client, **body):
    body.setdefault('local_date', '2026-08-24')
    return client.post('/api/workouts', json=body).get_json()


def log(client, workout_id, exercise_id, weight, reps, **kw):
    return client.post(f'/api/workouts/{workout_id}/sets',
                       json={'exercise_id': exercise_id, 'weight': weight,
                             'reps': reps, **kw}).get_json()


def finish(client, workout_id):
    return client.patch(f'/api/workouts/{workout_id}',
                        json={'finish': True}).get_json()
