"""Test harness: point the app at a throwaway DB, never at a real training log."""
import os
import sys
import tempfile

# Set BEFORE importing app — DB_PATH and the cookie policy are resolved at
# import time.
_TMP = tempfile.mkdtemp(prefix='workout_test_')
os.environ['WORKOUT_DATA_DIR'] = _TMP
os.environ['WORKOUT_PASSWORD'] = 'test-only-password'
os.environ['WORKOUT_SESSION_SECRET'] = 'test-only-secret'
os.environ.pop('RAILWAY_ENVIRONMENT', None)

# The app's own directory, and deliberately NOT the repo root: this app must
# keep working as a folder on its own.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import app as appmod  # noqa: E402
import auth as authmod  # noqa: E402

import pytest  # noqa: E402

PASSWORD = 'test-only-password'

# Every table the app writes. The temp DB is created once per session, not per
# test, so a table missing here leaks state between tests — the same trap
# salescrm/tests/conftest.py documents.
TABLES = ['sets', 'workouts', 'routine_items', 'routines', 'exercise_hidden',
          'settings', 'login_fails']


def _wipe():
    with appmod.get_db() as db:
        for t in TABLES:
            db.execute(f'DELETE FROM {t}')
        # Custom movements go too, but the seeded library stays: a fresh library
        # on every test is what init_db() would give a new install anyway.
        db.execute('DELETE FROM exercises WHERE is_custom=1')


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
    return sign_in(anon)


def sign_in(client, password=PASSWORD):
    """Sign in the way a browser does — through the real login form."""
    client.post('/login', data={'password': password})
    return client


def as_user(app, username):
    """A client signed in as an arbitrary user name.

    The app has one password and one owner, but every row carries a `user`, so
    the scoping tests need a way to be somebody else. This writes the session
    key the login writes, which is what proves the scoping is real rather than
    an artefact of there only ever being one name.
    """
    client = app.app.test_client()
    with client.session_transaction() as s:
        s['lift_user'] = username
    return client


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
