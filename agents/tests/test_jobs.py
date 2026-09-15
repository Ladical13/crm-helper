"""Cross-worker job ownership and recovery; no providers are contacted."""
import json
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from agents import config, jobs


def test_only_one_concurrent_claim_wins():
    # Seed schema before racing the claims, as it is on a running service.
    config.get_cache_db().close()
    def attempt():
        try:
            return jobs.claim('seo', True)
        except jobs.Busy:
            return None
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: attempt(), range(2)))
    assert sum(x is not None for x in results) == 1


def test_progress_and_preview_results_survive_a_new_process():
    job_id = jobs.claim('seo', True)
    manifest = {'ok': True, 'recommendations': [{'action': 'Fix title'}]}
    jobs.execute(job_id, lambda: manifest)
    result = subprocess.run(
        [sys.executable, '-c',
         'import json; from agents import jobs; print(json.dumps(jobs.get()))'],
        cwd=Path(__file__).resolve().parents[2], capture_output=True, text=True, check=True)
    state = json.loads(result.stdout)
    assert state['job_id'] == job_id
    assert state['dry_run'] is True
    assert state['manifest'] == manifest
    # A new job does not overwrite the result the first caller is polling.
    jobs.claim('social')
    assert jobs.get(job_id)['manifest'] == manifest


def test_dead_worker_expires_and_cannot_overwrite_its_error():
    old = jobs.claim('seo')
    with config.get_cache_db() as db:
        db.execute("UPDATE background_jobs SET heartbeat_at='2000-01-01T00:00:00Z'")
    newer = jobs.claim('social')
    assert jobs.get(old)['status'] == 'error'
    jobs.finish(old, {'ok': True})
    assert jobs.get(old)['status'] == 'error'
    assert jobs.get(newer)['running'] is True


def test_failed_worker_releases_slot_and_reports_error():
    def fail():
        raise RuntimeError('provider unavailable')
    job_id = jobs.claim('social')
    jobs.execute(job_id, fail)
    assert 'provider unavailable' in jobs.get(job_id)['manifest']['error']
    assert jobs.claim('seo') != job_id


def test_thread_start_failure_releases_slot(monkeypatch):
    def fail(*a, **kw):
        raise RuntimeError('no thread')
    monkeypatch.setattr(jobs.threading.Thread, 'start', fail)
    with pytest.raises(RuntimeError, match='no thread'):
        jobs.start('seo', lambda: {})
    assert jobs.get()['status'] == 'error'
    assert jobs.claim('social')


def test_scheduler_waits_for_manual_job_without_consuming_schedule(monkeypatch):
    from datetime import datetime
    from agents import scheduler
    scheduler.ensure_jobs()
    scheduler.set_enabled('content_listen', False)
    calls = []
    monkeypatch.setitem(scheduler.JOBS, 'seo_weekly', lambda: calls.append('ran') or 'done')
    job_id = jobs.claim('social')
    now = datetime(2026, 9, 14, 6, 30)
    assert scheduler.run_due(now) == []
    scheduled = next(j for j in scheduler.list_jobs() if j['name'] == 'seo_weekly')
    assert scheduled['last_run_at'] == ''
    jobs.finish(job_id, {'ok': True})
    assert scheduler.run_due(now) == ['seo_weekly']
    assert calls == ['ran']
