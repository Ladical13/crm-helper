"""Test harness: point the app at a throwaway DB, never at real data."""
import os
import sys
import tempfile

# Must be set BEFORE importing app (DB_PATH is resolved at import time).
_TMP = tempfile.mkdtemp(prefix='salescrm_test_')
os.environ.setdefault('SALESCRM_DATA_DIR', _TMP)
# Accounts live in the portal's shared store now, not salescrm.db.
os.environ.setdefault('PORTAL_DATA_DIR', _TMP)
os.environ.pop('BASE44_TOKEN', None)   # ensure Den calls degrade gracefully

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import app as appmod  # noqa: E402

import pytest  # noqa: E402

from portal import session as psession  # noqa: E402
from portal import users as pusers      # noqa: E402

# Every table the app writes. The temp DB is created once per session, not per
# test, so a table missing here leaks state between tests.
TABLES = ['leads', 'activities', 'tasks', 'cadence_enrollments',
          'coaching_notes', 'goals', 'documents', 'suppressions']


def _wipe():
    with appmod.get_db() as db:
        for t in TABLES:
            db.execute(f'DELETE FROM {t}')
    # Identity lives in the portal store, so it has to be reset here too or
    # the "first user bootstraps as admin" rule leaks across tests.
    with pusers.get_db() as db:
        db.execute('DELETE FROM users')
        db.execute('DELETE FROM invites')


@pytest.fixture
def app():
    return appmod


@pytest.fixture
def client(app):
    _wipe()
    app.app.config['TESTING'] = True
    return app.app.test_client()


# ── Identity helpers ─────────────────────────────────────────────────────────
# Signup and login moved to the portal when the three tools merged onto one
# origin. These tests are about pipeline behaviour, not about how identity gets
# established, so the helpers do directly what the portal's login does: create
# the account, then write the shared session keys.

def signup(client, username='luke', pw='secret1', code='TEST'):
    """Create an account and sign `client` in as it. Returns /api/me."""
    role = 'admin' if pusers.count() == 0 else 'rep'   # first user bootstraps as admin
    pusers.create(username, password=pw, role=role, full_name=username.title())
    return login(client, username)


def login(client, username='luke'):
    with client.session_transaction() as s:
        psession.sign_in(s, pusers.get(username))
    return client.get('/api/me')


def logout(client):
    with client.session_transaction() as s:
        s.clear()


def new_lead(client, **kw):
    body = {'first_name': 'Test', 'last_name': 'Lead', 'lead_type': 'homeowner',
            'source': 'referral', 'est_value': 10000, 'temperature': 'warm'}
    body.update(kw)
    return client.post('/api/leads', json=body).get_json()
