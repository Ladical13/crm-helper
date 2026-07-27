"""Shared test fixtures.

app.py resolves DATA_DIR into module-level constants at import time, so the env
var MUST be set before `import app` — hence the module-level setup below rather
than a fixture.
"""
import os
import sys
import tempfile

TEST_DATA_DIR = tempfile.mkdtemp(prefix='estimator-tests-')
os.makedirs(os.path.join(TEST_DATA_DIR, 'estimates'), exist_ok=True)
os.makedirs(os.path.join(TEST_DATA_DIR, 'uploads'), exist_ok=True)

os.environ['DATA_DIR'] = TEST_DATA_DIR
os.environ['SESSION_SECRET'] = 'test-only-secret'
# Accounts live in the portal's shared store now, not DATA_DIR/users.json.
os.environ['PORTAL_DATA_DIR'] = TEST_DATA_DIR
os.environ['PORTAL_SIGNUP_CODE'] = 'test-only-code'

ESTIMATOR_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ESTIMATOR_DIR)

# Price book / tier defaults are read from DATA_DIR; copy the real ones in so
# tests exercise the shipped configuration.
import shutil
for _f in ('price_book.json', 'tier_defaults.json', 'company_content.json'):
    _src = os.path.join(ESTIMATOR_DIR, _f)
    if os.path.exists(_src):
        shutil.copy(_src, os.path.join(TEST_DATA_DIR, _f))

import pytest
import app as estimator_app
from portal import users as portal_users

# Roles come from the portal store, so the admin the tests act as has to exist
# there. Seeded once at import: the store is a file in TEST_DATA_DIR and the
# tests never mutate luke's role.
if not portal_users.get('luke'):
    portal_users.create('luke', password='test-only-password', role='admin',
                        full_name='Luke Durnbaugh')


@pytest.fixture
def app():
    estimator_app.app.config['TESTING'] = True
    return estimator_app.app


@pytest.fixture
def anon(app):
    """Client with no session — for auth-guard tests."""
    return app.test_client()


@pytest.fixture
def client(app):
    """Client logged in as an admin."""
    c = app.test_client()
    with c.session_transaction() as s:
        s['user'] = 'luke'
    return c


@pytest.fixture
def A():
    """The app module itself, for unit-testing pricing helpers."""
    return estimator_app
