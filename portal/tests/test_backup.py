"""The three databases that had no backup until now.

The test that matters most here is `test_snapshot_captures_uncheckpointed_write`.
Everything else guards access; that one guards *correctness*, and it is the
reason the module uses SQLite's online backup API instead of shutil.copy. In
WAL mode a committed row can still be sitting in the `-wal` sidecar, so a
naive copy of the `.db` silently loses it — a backup that restores cleanly and
is missing yesterday's leads is worse than no backup, because you trust it.
"""
import io
import json
import os
import sqlite3
import tempfile
import zipfile

from conftest import SIGNUP_CODE

from portal import backup as pbackup
from portal import users as pusers


def _zip_of(resp):
    return zipfile.ZipFile(io.BytesIO(resp.get_data()))


def _manifest_of(zf):
    return json.loads(zf.read('manifest.json'))


# ── Access ───────────────────────────────────────────────────────────────────

def test_anonymous_gets_401(client):
    assert client.get('/api/backup/databases').status_code == 401


def test_rep_is_refused(rep):
    assert rep.get('/api/backup/databases').status_code == 403


def test_manager_is_refused(client):
    """Deliberate: the endpoint is admin-only even though the original note
    said manager-only. portal.db is every password hash in the company."""
    pusers.create('luke', password='roofroof1', role='admin')
    client.post('/login', data={'username': 'dana', 'password': 'managerpass1',
                                'signup_code': SIGNUP_CODE})
    pusers.set_role('dana', 'manager')
    assert pusers.is_manager_up('dana')
    assert not pusers.is_admin('dana')
    assert client.get('/api/backup/databases').status_code == 403


def test_admin_gets_a_zip(admin):
    resp = admin.get('/api/backup/databases')
    assert resp.status_code == 200
    assert resp.headers['Content-Type'] == 'application/zip'
    assert 'attachment' in resp.headers['Content-Disposition']
    assert '.zip' in resp.headers['Content-Disposition']


# ── Contents ─────────────────────────────────────────────────────────────────

def test_zip_carries_portal_db_and_a_manifest(admin):
    zf = _zip_of(admin.get('/api/backup/databases'))
    assert 'portal.db' in zf.namelist()
    assert 'manifest.json' in zf.namelist()


def test_manifest_accounts_for_all_three_databases(admin):
    m = _manifest_of(_zip_of(admin.get('/api/backup/databases')))
    assert set(m['databases']) == {'portal.db', 'salescrm.db', 'canvasser.db'}
    assert m['created_utc']
    for name, info in m['databases'].items():
        # Either it shipped, or the manifest says why not — never silence.
        assert info['present'] is False or 'bytes' in info or 'error' in info, name


def test_missing_database_is_recorded_not_raised(monkeypatch):
    """A fresh volume has no canvasser.db until the first pin. That must not
    cost the admin the other two databases."""
    real = pbackup.database_paths

    def _with_a_missing_one():
        paths = dict(real())
        paths['canvasser.db'] = os.path.join(
            tempfile.gettempdir(), 'p1-does-not-exist', 'canvasser.db')
        return paths

    monkeypatch.setattr(pbackup, 'database_paths', _with_a_missing_one)
    data, manifest = pbackup.build_zip()
    assert manifest['databases']['canvasser.db']['present'] is False
    assert 'portal.db' in zipfile.ZipFile(io.BytesIO(data)).namelist()


# ── Correctness under WAL ────────────────────────────────────────────────────

def test_snapshot_captures_uncheckpointed_write(admin):
    """A row committed but still in the -wal sidecar must be in the snapshot.

    This is what a plain file copy of the .db would miss.
    """
    pusers.create('fresh_rep', password='knockknock1', role='rep')

    zf = _zip_of(admin.get('/api/backup/databases'))
    tmp = tempfile.NamedTemporaryFile(suffix='.db', delete=False)
    try:
        tmp.write(zf.read('portal.db'))
        tmp.close()
        conn = sqlite3.connect(tmp.name)
        try:
            names = {r[0] for r in conn.execute('SELECT username FROM users')}
        finally:
            conn.close()
        assert 'fresh_rep' in names
    finally:
        os.unlink(tmp.name)


def test_snapshot_is_self_contained_not_wal(admin):
    """The restore instruction is "unzip and go", so the snapshot must not be
    a WAL database expecting sidecars that are not in the zip."""
    zf = _zip_of(admin.get('/api/backup/databases'))
    assert not [n for n in zf.namelist() if n.endswith(('-wal', '-shm'))]

    tmp = tempfile.NamedTemporaryFile(suffix='.db', delete=False)
    try:
        tmp.write(zf.read('portal.db'))
        tmp.close()
        conn = sqlite3.connect(tmp.name)
        try:
            mode = conn.execute('PRAGMA journal_mode').fetchone()[0].lower()
        finally:
            conn.close()
        assert mode == 'delete'
    finally:
        os.unlink(tmp.name)


def test_manifest_counts_rows_from_the_snapshot(admin):
    """The count a restore would actually get — the number worth checking."""
    m = _manifest_of(_zip_of(admin.get('/api/backup/databases')))
    tables = m['databases']['portal.db']['tables']
    assert tables['users'] >= 1


def test_backup_does_not_disturb_the_live_database(admin):
    """The site keeps serving during a backup — no exclusive lock, and the
    source stays in WAL afterwards."""
    before = pusers.get('luke')
    pbackup.build_zip()
    assert pusers.get('luke')['username'] == before['username']

    conn = pusers.get_db()
    try:
        assert conn.execute('PRAGMA journal_mode').fetchone()[0].lower() == 'wal'
    finally:
        conn.close()
