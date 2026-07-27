"""The one-shot merge of three user stores into one.

This runs once against real production data, so the cost of a bug is a rep who
cannot sign in on the morning of the cutover. Precedence and idempotency are
the two things worth pinning down.
"""
import json
import sqlite3

import pytest
from werkzeug.security import generate_password_hash

from portal import migrate_users, users as pusers


@pytest.fixture
def stores(tmp_path, monkeypatch):
    """Three source stores with the same person holding three passwords."""
    est, canv, crm, portal_dir = (tmp_path / n for n in ('est', 'canv', 'crm', 'portal'))
    for d in (est, canv, crm, portal_dir):
        d.mkdir()

    (est / 'users.json').write_text(json.dumps({
        'luke':  {'pw_hash': generate_password_hash('estimator-luke'), 'is_admin': True},
        'phil':  {'pw_hash': generate_password_hash('estimator-phil'), 'is_admin': False},
        'ghost': {'is_admin': False},          # invited, never enrolled
    }))

    db = sqlite3.connect(canv / 'canvasser.db')
    db.executescript('CREATE TABLE users (username TEXT PRIMARY KEY, pw_hash TEXT NOT NULL,'
                     ' is_admin INTEGER DEFAULT 0, created_at TEXT NOT NULL)')
    db.executemany('INSERT INTO users VALUES (?,?,?,?)', [
        ('luke',  generate_password_hash('canvasser-luke'),  1, 'x'),
        ('derik', generate_password_hash('canvasser-derik'), 0, 'x'),
    ])
    db.commit(); db.close()

    db = sqlite3.connect(crm / 'salescrm.db')
    db.executescript("CREATE TABLE users (username TEXT PRIMARY KEY, pw_hash TEXT NOT NULL,"
                     " is_admin INTEGER DEFAULT 0, role TEXT DEFAULT 'rep',"
                     " full_name TEXT DEFAULT '', created_at TEXT NOT NULL)")
    db.executemany('INSERT INTO users VALUES (?,?,?,?,?,?)', [
        ('luke',  generate_password_hash('crm-luke'),  1, 'admin',   'Luke D',  'x'),
        ('casey', generate_password_hash('crm-casey'), 1, 'manager', 'Casey R', 'x'),
    ])
    db.commit(); db.close()

    monkeypatch.setenv('DATA_DIR', str(est))
    monkeypatch.setenv('CANVASSER_DATA_DIR', str(canv))
    monkeypatch.setenv('SALESCRM_DATA_DIR', str(crm))
    monkeypatch.setenv('PORTAL_DATA_DIR', str(portal_dir))
    pusers.reset_cache()
    yield
    pusers.reset_cache()


def test_dry_run_writes_nothing(stores):
    migrate_users.main([])
    assert pusers.count() == 0


def test_everyone_carries_over_exactly_once(stores):
    migrate_users.main(['--apply'])
    assert {u['username'] for u in pusers.all_users()} == {'luke', 'phil', 'derik', 'casey'}


def test_unenrolled_users_are_skipped(stores):
    """A users.json record with no pw_hash was an invitation, not an account."""
    migrate_users.main(['--apply'])
    assert pusers.get('ghost') is None


def test_estimator_password_wins(stores):
    """The decided precedence: the app reps are in daily sets the one password."""
    migrate_users.main(['--apply'])
    assert pusers.verify('luke', 'estimator-luke')
    assert not pusers.verify('luke', 'canvasser-luke')
    assert not pusers.verify('luke', 'crm-luke')


def test_users_from_only_one_app_keep_their_own_password(stores):
    migrate_users.main(['--apply'])
    assert pusers.verify('phil', 'estimator-phil')
    assert pusers.verify('derik', 'canvasser-derik')
    assert pusers.verify('casey', 'crm-casey')


def test_roles_survive(stores):
    migrate_users.main(['--apply'])
    assert pusers.role_of('luke') == 'admin'
    assert pusers.role_of('casey') == 'manager'     # not flattened to admin or rep
    assert pusers.role_of('phil') == 'rep'
    assert pusers.is_manager_up('casey') and not pusers.is_admin('casey')


def test_rerunning_changes_nothing(stores):
    migrate_users.main(['--apply'])
    before = {u['username']: u['pw_hash'] for u in pusers.all_users()}
    migrate_users.main(['--apply'])
    after = {u['username']: u['pw_hash'] for u in pusers.all_users()}
    assert before == after


def test_rerunning_does_not_clobber_a_password_changed_since(stores):
    """A rep who changed their password after the cutover keeps the new one."""
    migrate_users.main(['--apply'])
    pusers.set_password('luke', 'chosen-after-migration')
    migrate_users.main(['--apply'])
    assert pusers.verify('luke', 'chosen-after-migration')


def test_force_does_overwrite(stores):
    migrate_users.main(['--apply'])
    pusers.set_password('luke', 'chosen-after-migration')
    migrate_users.main(['--apply', '--force'])
    assert pusers.verify('luke', 'estimator-luke')


def test_conflicts_are_reported(stores, capsys):
    migrate_users.main([])
    out = capsys.readouterr().out
    assert 'conflict' in out.lower()
    assert 'password' in out           # luke holds three different passwords
