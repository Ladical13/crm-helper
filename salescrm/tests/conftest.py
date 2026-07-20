"""Test harness: point the app at a throwaway DB, never at real data."""
import os
import sys
import tempfile

# Must be set BEFORE importing app (DB_PATH is resolved at import time).
os.environ.setdefault('SALESCRM_DATA_DIR', tempfile.mkdtemp(prefix='salescrm_test_'))
os.environ.setdefault('SALESCRM_SIGNUP_CODE', 'TEST')
os.environ.pop('BASE44_TOKEN', None)   # ensure Den calls degrade gracefully

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import app as appmod  # noqa: E402

import pytest  # noqa: E402

TABLES = ['users', 'invites', 'leads', 'activities', 'tasks',
          'cadence_enrollments', 'coaching_notes', 'goals']


def _wipe():
    with appmod.get_db() as db:
        for t in TABLES:
            db.execute(f'DELETE FROM {t}')


@pytest.fixture
def app():
    return appmod


@pytest.fixture
def client(app):
    _wipe()
    app.app.config['TESTING'] = True
    return app.app.test_client()


def signup(client, username='luke', pw='secret1', code='TEST'):
    return client.post('/api/signup', json={
        'signup_code': code, 'username': username, 'password': pw, 'full_name': username.title()})


def new_lead(client, **kw):
    body = {'first_name': 'Test', 'last_name': 'Lead', 'lead_type': 'homeowner',
            'source': 'referral', 'est_value': 10000, 'temperature': 'warm'}
    body.update(kw)
    return client.post('/api/leads', json=body).get_json()
