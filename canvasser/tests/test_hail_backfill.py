"""Filling the archive from the admin UI.

The archive is the company's primary data product and it shipped EMPTY.
`hail/backfill.py` said "what a nightly cron runs" in its own docstring and no
cron ran it, so every address lookup fell through to the SPC spotter reports —
call-ins near a house rather than measurements of it. The cost was not
theoretical: MESH holds a 1.91-inch storm over Loveland on 2024-07-21 and the
tool answered "no hail in five years" about a roof inside that swath, because
the database it asked had nothing in it at all.

The nightly job keeps the archive current from now on. These tests cover the
other half — history — which nothing else can supply, and which has to run on
the server because the archive lives on the Railway volume.
"""
import os
import sys
import time
from datetime import timedelta

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)))))

from hail import grid as hgrid      # noqa: E402
from hail import storms             # noqa: E402
from portal import clock            # noqa: E402


class _Swath(list):
    """Truthy with cells, falsy on a quiet day. Both get recorded."""
    max_size = 1.5

    def cells(self):
        return []


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv('CANVASSER_DATA_DIR', str(tmp_path))
    monkeypatch.setenv('PORTAL_DATA_DIR', str(tmp_path))
    monkeypatch.setenv('HAIL_DATA_DIR', str(tmp_path))
    monkeypatch.setenv('SESSION_SECRET', 'test-secret')
    storms.reset_cache()
    for mod in [m for m in list(sys.modules) if m in ('app', 'p1_canvass_app')]:
        del sys.modules[mod]
    import app as canvasser_app
    canvasser_app.app.config['TESTING'] = True
    with canvasser_app.app.test_client() as c:
        with c.session_transaction() as sess:
            sess['username'] = 'luke'
        yield c, canvasser_app


@pytest.fixture()
def as_manager(monkeypatch):
    import app as canvasser_app
    monkeypatch.setattr(canvasser_app.pusers, 'is_manager_up', lambda u: True)


@pytest.fixture()
def fake_noaa(monkeypatch):
    """No network. Records which dates were asked for."""
    from hail import ingest
    asked = []

    def swath_for(d, **kw):
        asked.append(d)
        return _Swath(['c']) if d.day % 2 else _Swath()

    monkeypatch.setattr(ingest, 'swath_for', swath_for)
    monkeypatch.setattr(storms, 'record', lambda *a, **k: None)
    return asked


def _wait(c, timeout=5):
    """Poll until the background thread finishes. The endpoint is 202 by
    design — a season is minutes and gunicorn kills a worker at 60 seconds."""
    for _ in range(int(timeout / 0.02)):
        st = c.get('/api/hail/backfill').get_json()
        if (st.get('job') or {}).get('status') not in (None, 'running'):
            return st
        time.sleep(0.02)
    raise AssertionError('backfill did not finish')


# ── who may run it ─────────────────────────────────────────────────────

def test_a_rep_cannot_start_a_backfill(client):
    """Reps see the map; filling the archive is not theirs to start."""
    c, _ = client
    assert c.post('/api/hail/backfill?days=3').status_code == 403
    assert c.get('/api/hail/backfill').status_code == 403


def test_a_signed_out_browser_cannot_either(client):
    c, _ = client
    with c.session_transaction() as sess:
        sess.clear()
    assert c.post('/api/hail/backfill?days=3').status_code == 401


# ── the run ────────────────────────────────────────────────────────────

def test_a_backfill_runs_in_the_background_and_reports_progress(
        client, as_manager, fake_noaa):
    c, _ = client
    r = c.post('/api/hail/backfill?days=4')
    assert r.status_code == 202, r.get_json()
    assert r.get_json()['days'] == 4

    st = _wait(c)
    assert st['job']['status'] == 'done'
    assert st['job']['done'] == 4
    assert st['job']['total'] == 4
    assert len(fake_noaa) == 4


def test_days_already_held_are_skipped(client, as_manager, monkeypatch):
    """What makes the button re-runnable: an admin who hits it twice, or hits
    it again after a failure, does not pay for the days that already landed."""
    c, _ = client
    # The COMPANY's today, because that is what the endpoint counts back from.
    # A UTC `date.today()` here agrees for eighteen hours a day and disagrees
    # for the six after 6pm Mountain — a test that passes if you run it again.
    today = clock.company_today()
    held = {(today - timedelta(days=i)).isoformat() for i in range(3)}
    monkeypatch.setattr(storms, 'ingested_dates', lambda *a, **k: held)

    r = c.post('/api/hail/backfill?days=3')
    assert r.get_json()['status'] == 'nothing_to_do'


def test_a_second_backfill_is_refused_while_one_is_running(client, as_manager,
                                                           monkeypatch):
    """Two gunicorn workers, one archive. Without a claim both would fetch the
    same days and write over each other."""
    c, _ = client
    monkeypatch.setattr(storms, 'backfill_claim', lambda *a, **k: False)
    r = c.post('/api/hail/backfill?days=3')
    assert r.status_code == 409
    assert 'already running' in r.get_json()['error']


def test_one_bad_day_does_not_lose_the_rest(client, as_manager, monkeypatch):
    """A failed day stays unrecorded so the next run picks it up, rather than
    being written as a quiet day — the distinction the archive rests on."""
    c, _ = client
    from hail import ingest
    today = clock.company_today()
    bad = today - timedelta(days=2)

    def swath_for(d, **kw):
        if d == bad:
            raise RuntimeError('NOAA said no')
        return _Swath(['c'])

    written = []
    monkeypatch.setattr(ingest, 'swath_for', swath_for)
    monkeypatch.setattr(storms, 'record', lambda date, swath, **k: written.append(date))

    c.post('/api/hail/backfill?days=4')
    st = _wait(c)
    assert st['job']['status'] == 'done_with_failures'
    assert st['job']['failures'] == 1
    assert 'retried' in st['job']['note']
    # The load-bearing half: the failed day is NOT written. A day recorded with
    # no swath reads as "radar looked and saw nothing", which is the one answer
    # safe to repeat on a doorstep — and it would be a lie about a day nobody
    # managed to fetch.
    assert bad.isoformat() not in written
    assert len(written) == 3


# ── the range ──────────────────────────────────────────────────────────

def test_a_season_pulls_only_the_severe_months(client, as_manager, fake_noaa):
    """A full year spends most of its time on days that never had a storm."""
    c, _ = client
    from hail import backfill as hbackfill
    c.post('/api/hail/backfill?season=2024')
    _wait(c, timeout=15)
    months = {d.month for d in fake_noaa}
    assert months and months <= set(hbackfill.SEASON_MONTHS)
    assert {d.year for d in fake_noaa} == {2024}


def test_a_range_before_the_archive_exists_is_refused_not_silently_empty(
        client, as_manager):
    """MRMS on AWS starts 2020-10-14. Asking for 2015 is a real mistake and
    reporting "0 days ingested" would read as success."""
    c, _ = client
    r = c.post('/api/hail/backfill?season=2015')
    assert r.status_code == 400
    from hail import ingest
    assert ingest.EARLIEST.isoformat() in r.get_json()['error']


# ── what the admin is told ─────────────────────────────────────────────

def test_the_state_says_how_much_archive_exists(client, as_manager):
    """An admin's real question is "is there enough in here to trust it", and
    a job status alone cannot answer that."""
    c, _ = client
    swath = hgrid.swath_from_points([(40.5, -105.0, 40.0)], threshold_in=1.0)
    storms.record('2024-07-21', swath)
    st = c.get('/api/hail/backfill').get_json()
    assert st['archive']['days_held'] == 1
    assert st['archive']['first'] == '2024-07-21'


def test_state_is_readable_before_any_backfill_has_ever_run(client, as_manager):
    """The empty archive is exactly the state this exists to get out of, so
    the screen that reports it must not 500 on the way there."""
    c, _ = client
    st = c.get('/api/hail/backfill').get_json()
    assert st['job'] is None
    assert st['archive']['days_held'] == 0
