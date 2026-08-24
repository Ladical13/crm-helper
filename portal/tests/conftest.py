"""Test harness for the merged portal.

Every env var has to be set before importing portal.wsgi, because that import
loads all three sub-apps and each of them freezes its DATA_DIR into module
constants at import time.
"""
import os
import sys
import tempfile

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, REPO_ROOT)

TMP = tempfile.mkdtemp(prefix='portal-tests-')
for sub in ('estimator', 'canvasser', 'crm', 'workout'):
    os.makedirs(os.path.join(TMP, sub), exist_ok=True)
os.makedirs(os.path.join(TMP, 'estimator', 'estimates'), exist_ok=True)
os.makedirs(os.path.join(TMP, 'estimator', 'uploads'), exist_ok=True)

os.environ['PORTAL_DATA_DIR'] = TMP
os.environ['DATA_DIR'] = os.path.join(TMP, 'estimator')
os.environ['CANVASSER_DATA_DIR'] = os.path.join(TMP, 'canvasser')
os.environ['SALESCRM_DATA_DIR'] = os.path.join(TMP, 'crm')
os.environ['WORKOUT_DATA_DIR'] = os.path.join(TMP, 'workout')
os.environ['SESSION_SECRET'] = 'test-only-secret'
os.environ['PORTAL_SIGNUP_CODE'] = 'test-only-code'
os.environ.pop('BASE44_TOKEN', None)
os.environ.pop('DISABLE_AUTH', None)

import pytest  # noqa: E402

from portal import throttle as pthrottle  # noqa: E402
from portal import users as pusers  # noqa: E402
from portal.wsgi import application  # noqa: E402  (loads every sub-app)


SIGNUP_CODE = 'test-only-code'


def _wipe():
    with pusers.get_db() as db:
        db.execute('DELETE FROM users')
        db.execute('DELETE FROM invites')
    # Every test in the suite logs in from the same 127.0.0.1, so without this
    # the per-IP failure budget accumulates across unrelated cases and the last
    # tests in a run start getting 429s.
    pthrottle.reset_all()


@pytest.fixture
def client():
    """Client for the whole composed site, not one app."""
    from werkzeug.test import Client
    _wipe()
    return Client(application)


@pytest.fixture
def admin(client):
    """Signed in as the first user, which bootstraps as admin."""
    client.post('/login', data={'username': 'luke', 'password': 'roofroof1',
                                'signup_code': SIGNUP_CODE})
    return client


@pytest.fixture
def rep(client):
    """Signed in as a second, non-admin user."""
    pusers.create('luke', password='roofroof1', role='admin')   # occupy first slot
    client.post('/login', data={'username': 'bryan', 'password': 'knockknock',
                                'signup_code': SIGNUP_CODE})
    return client
