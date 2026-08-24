"""Test harness: point the app at a throwaway DB, never at a real training log."""
import os
import sys
import tempfile

# Set BEFORE importing app — DB_PATH is resolved at import time.
_TMP = tempfile.mkdtemp(prefix='workout_test_')
os.environ['WORKOUT_DATA_DIR'] = _TMP

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import app as appmod  # noqa: E402

import pytest  # noqa: E402

# Every table the app writes. The temp DB is created once per session, not per
# test, so a table missing here leaks state between tests — the same trap
# salescrm/tests/conftest.py documents.
TABLES = ['sets', 'workouts', 'routine_items', 'routines']


def _wipe():
    with appmod.get_db() as db:
        for t in TABLES:
            db.execute(f'DELETE FROM {t}')
        # Custom exercises go too, but the seeded library stays: a fresh library
        # on every test is what init_db() would give a new install anyway.
        db.execute('DELETE FROM exercises WHERE is_custom=1')
        db.execute('UPDATE exercises SET archived=0')


@pytest.fixture
def app():
    return appmod


@pytest.fixture
def client(app):
    _wipe()
    app.app.config['TESTING'] = True
    return app.app.test_client()


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
