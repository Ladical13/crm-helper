"""Marketing job progress shared by every web worker and the scheduler.

The SQLite lease prevents overlapping jobs; a heartbeat distinguishes a slow
run from a worker killed by a deploy. Generated content remains draft-only.
"""
import json
import sqlite3
import threading
from datetime import datetime, timedelta

from . import config

HEARTBEAT_SECONDS = 30
LEASE_SECONDS = 300


class Busy(RuntimeError):
    pass


def _reap(db):
    cutoff = (datetime.utcnow() - timedelta(seconds=LEASE_SECONDS)).strftime(
        '%Y-%m-%dT%H:%M:%SZ')
    db.execute(
        "UPDATE background_jobs SET status='error', finished_at=?, manifest=? "
        "WHERE status='running' AND heartbeat_at < ?",
        (config.now_iso(), json.dumps({'ok': False, 'error':
         'The worker stopped responding, possibly after a server restart. Run again.'}), cutoff))


def claim(kind, dry_run=False):
    """Reserve the one marketing slot before starting a worker thread."""
    with config.get_cache_db() as db:
        _reap(db)
        try:
            cur = db.execute(
                'INSERT INTO background_jobs (kind,dry_run,started_at,heartbeat_at) '
                'VALUES (?,?,?,?)',
                (kind, int(dry_run), config.now_iso(), config.now_iso()))
        except sqlite3.IntegrityError:
            raise Busy('a run is already in progress') from None
        return cur.lastrowid


def get(job_id=None):
    with config.get_cache_db() as db:
        _reap(db)
        if job_id is None:
            row = db.execute('SELECT * FROM background_jobs ORDER BY id DESC LIMIT 1').fetchone()
        else:
            row = db.execute('SELECT * FROM background_jobs WHERE id=?', (job_id,)).fetchone()
    if row is None:
        return None
    return {'job_id': row['id'], 'kind': row['kind'],
            'running': row['status'] == 'running', 'status': row['status'],
            'stage': row['kind'] if row['status'] == 'running' else 'done',
            'dry_run': bool(row['dry_run']),
            'manifest': json.loads(row['manifest']) if row['manifest'] else None}


def finish(job_id, manifest):
    with config.get_cache_db() as db:
        db.execute(
            "UPDATE background_jobs SET status=?, finished_at=?, manifest=? "
            "WHERE id=? AND status='running'",
            ('ok' if manifest.get('ok') else 'error', config.now_iso(),
             json.dumps(manifest, default=str), job_id))


def _heartbeat(job_id, stop):
    while not stop.wait(HEARTBEAT_SECONDS):
        with config.get_cache_db() as db:
            cur = db.execute("UPDATE background_jobs SET heartbeat_at=? "
                             "WHERE id=? AND status='running'", (config.now_iso(), job_id))
        if not cur.rowcount:
            return


def execute(job_id, work):
    """Blocking execution for either a request's thread or the scheduler."""
    stop = threading.Event()
    heartbeat = threading.Thread(target=_heartbeat, args=(job_id, stop), daemon=True)
    try:
        heartbeat.start()
        manifest = work()
        if not isinstance(manifest, dict):
            manifest = {'note': str(manifest)}
        manifest.setdefault('ok', True)
    except Exception as exc:
        manifest = {'ok': False, 'error': f'{type(exc).__name__}: {exc}'}
    finally:
        stop.set()
        if heartbeat.ident is not None:
            heartbeat.join()
    finish(job_id, manifest)
    return manifest


def start(kind, work, dry_run=False):
    job_id = claim(kind, dry_run)
    try:
        threading.Thread(target=execute, args=(job_id, work), daemon=True).start()
    except Exception as exc:
        finish(job_id, {'ok': False, 'error': str(exc)})
        raise
    return job_id
