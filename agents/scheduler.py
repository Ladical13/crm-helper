"""Weekly job runner. Makes "weekly" mean weekly instead of "when someone remembers".

A background thread, not a cron service: the Procfile runs **one** Railway
service for the whole repo, and adding a second one to run a cron would break
that and cost money for a job that fires twice a week.

That means two gunicorn workers each start a scheduler, so the claim has to be
atomic or the weekly report runs twice. ``_claim`` does a conditional UPDATE
and checks ``rowcount`` — exactly one worker wins, in a single SQLite
statement, with no extra locking machinery.

Off by default. ``NIMBUS_SCHEDULER=1`` turns it on, so nothing starts doing
background work on a developer's laptop, or in a test run, by surprise.
"""
import os
import threading
import time
import traceback
from datetime import datetime

from . import config

# How often the thread wakes to check whether anything is due. The window only
# needs to be finer than an hour, since jobs are scheduled to the hour.
TICK_SECONDS = 600

DEFAULT_JOBS = [
    # name,           weekday (0=Mon), hour UTC
    ('seo_weekly',    0, 6),    # Monday 06:00 UTC — report ready before the day
    ('content_listen', 0, 5),   # an hour earlier, so the SEO run sees its topics
    ('social_weekly', 1, 6),    # Tuesday, after a human has read Monday's queue
]

_thread = None
_stop = threading.Event()


def enabled():
    return os.environ.get('NIMBUS_SCHEDULER', '').strip() in ('1', 'true', 'yes')


def ensure_jobs():
    """Seed the job rows once. Never overwrites a schedule someone changed."""
    with config.get_cache_db() as db:
        for name, weekday, hour in DEFAULT_JOBS:
            db.execute(
                'INSERT OR IGNORE INTO scheduled_jobs (name, weekday, hour_utc) '
                'VALUES (?, ?, ?)', (name, weekday, hour))
        db.commit()


def list_jobs():
    ensure_jobs()
    with config.get_cache_db() as db:
        return [dict(r) for r in db.execute(
            'SELECT * FROM scheduled_jobs ORDER BY name')]


def set_enabled(name, on):
    # Seed first: on a fresh volume the rows do not exist until something asks
    # for them, and toggling a job before anyone has opened the page would
    # otherwise 404 on a job that plainly exists.
    ensure_jobs()
    with config.get_cache_db() as db:
        cur = db.execute('UPDATE scheduled_jobs SET enabled = ? WHERE name = ?',
                         (1 if on else 0, name))
        db.commit()
        return bool(cur.rowcount)


def _due(job, now):
    """True when this job should run and has not already run this cycle."""
    if not job['enabled']:
        return False
    if now.weekday() != int(job['weekday']) or now.hour < int(job['hour_utc']):
        return False
    last = job['last_run_at'] or ''
    if not last:
        return True
    try:
        last_dt = datetime.strptime(last, '%Y-%m-%dT%H:%M:%SZ')
    except ValueError:
        return True
    # Ran already today — done for this week.
    return last_dt.date() != now.date()


def _claim(name, now):
    """Atomically take the job. Exactly one worker gets True.

    The WHERE clause carries the same condition the reader used, so a second
    worker arriving a millisecond later updates zero rows and stands down.
    """
    stamp = now.strftime('%Y-%m-%dT%H:%M:%SZ')
    today = now.strftime('%Y-%m-%d')
    with config.get_cache_db() as db:
        cur = db.execute(
            'UPDATE scheduled_jobs SET last_run_at = ?, last_status = ? '
            'WHERE name = ? AND enabled = 1 '
            "AND (last_run_at = '' OR substr(last_run_at, 1, 10) != ?)",
            (stamp, 'running', name, today))
        db.commit()
        return bool(cur.rowcount)


def _finish(name, status, summary):
    with config.get_cache_db() as db:
        db.execute('UPDATE scheduled_jobs SET last_status = ?, last_summary = ? '
                   'WHERE name = ?', (status, str(summary)[:400], name))
        db.commit()


# ── The jobs themselves ──────────────────────────────────────────────────────

def _job_seo_weekly():
    from .seo import run as seo
    manifest = seo.run(dry_run=False)
    if not manifest.get('ok'):
        raise RuntimeError(manifest.get('error', 'unknown error'))
    return (f'{len(manifest.get("recommendations") or [])} recommendation(s), '
            f'{manifest.get("pages_crawled", 0)} pages, '
            f'${manifest.get("cost_usd", 0):.3f}')


def _job_content_listen():
    from .content import listen
    out = listen.run()
    return f'{len(out.get("topics") or [])} topic(s) — {out.get("note", "")}'


def _job_social_weekly():
    from .content import posts
    out = posts.weekly_run()
    return out.get('note', '')


JOBS = {
    'seo_weekly':     _job_seo_weekly,
    'content_listen': _job_content_listen,
    'social_weekly':  _job_social_weekly,
}


def run_due(now=None):
    """Run whatever is due. Returns the names actually run. Never raises."""
    now = now or datetime.utcnow()
    ensure_jobs()
    ran = []
    for job in list_jobs():
        name = job['name']
        if name not in JOBS or not _due(job, now):
            continue
        if not _claim(name, now):
            continue        # another worker got there first
        try:
            summary = JOBS[name]()
            _finish(name, 'ok', summary)
            ran.append(name)
        except Exception as e:                                   # noqa: BLE001
            _finish(name, 'error', f'{type(e).__name__}: {e}')
            print(f'[scheduler] {name} failed: {e}\n{traceback.format_exc()}')
    return ran


def _loop():
    while not _stop.is_set():
        try:
            run_due()
        except Exception as e:                                   # noqa: BLE001
            # The loop must outlive any single failure, or one bad week takes
            # the scheduler down until the next deploy.
            print(f'[scheduler] tick failed: {e}')
        _stop.wait(TICK_SECONDS)


def start():
    """Start the background thread. Idempotent, and a no-op unless enabled."""
    global _thread
    if not enabled():
        return False
    if _thread and _thread.is_alive():
        return True
    _stop.clear()
    _thread = threading.Thread(target=_loop, name='nimbus-scheduler', daemon=True)
    _thread.start()
    return True


def stop():
    _stop.set()
