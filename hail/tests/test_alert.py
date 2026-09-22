"""Telling somebody a storm landed.

The archive going in nightly is half of it. Nobody reads a database: a storm at
2am is worth knowing about at 6am, not whenever someone next opens the map and
thinks to check. This is the push.
"""
import datetime as dt

import pytest

from hail import alert, grid as hgrid, storms


@pytest.fixture(autouse=True)
def _archive(tmp_path, monkeypatch):
    monkeypatch.setenv('HAIL_DATA_DIR', str(tmp_path))
    monkeypatch.delenv('HAIL_ALERT_BOUNDS', raising=False)
    storms.reset_cache()
    yield


def _record(day, points, threshold=1.0):
    swath = hgrid.swath_from_points(points, threshold_in=threshold)
    storms.record(day, swath)


# Inside the service-area box, and well outside it.
IN_AREA  = (40.3978, -105.0750, 1.75)     # Loveland
FAR_AWAY = (39.2650, -102.9650, 3.20)     # out by Burlington


class _Mailer:
    """Stands in for the estimator's `_send_email`, which is injected."""

    def __init__(self, ok=True):
        self.ok, self.sent = ok, []

    def __call__(self, subject, html, to_addr, **kw):
        self.sent.append({'subject': subject, 'html': html, 'to': to_addr})
        return self.ok


# ── what counts as ours ────────────────────────────────────────────────

def test_a_storm_over_the_service_area_alerts():
    _record('2026-06-10', [IN_AREA])
    m = _Mailer()
    out = alert.send_new(m, 'luke@example.com', today=dt.date(2026, 6, 11))
    assert out['sent'] == 1
    assert 'Loveland' in m.sent[0]['html']


def test_a_storm_across_the_state_does_not():
    """Colorado gets 1-inch hail somewhere most days of the season. An alert
    that fires on all of them is one nobody reads by July."""
    _record('2026-06-10', [FAR_AWAY])
    m = _Mailer()
    out = alert.send_new(m, 'luke@example.com', today=dt.date(2026, 6, 11))
    assert out['sent'] == 0
    assert not m.sent


def test_the_brief_reports_only_the_cells_inside_the_area():
    """A statewide maximum is useless to a rep who works one county — and
    quoting it would overstate what landed here."""
    _record('2026-06-10', [IN_AREA, FAR_AWAY])
    evs = alert.pending(today=dt.date(2026, 6, 11))
    assert len(evs) == 1
    assert evs[0]['max_size'] == pytest.approx(1.75, abs=0.01)


def test_small_hail_does_not_wake_anyone():
    _record('2026-06-10', [(40.3978, -105.0750, 0.75)], threshold=0.5)
    assert alert.pending(today=dt.date(2026, 6, 11)) == []


# ── sent once, and only once ───────────────────────────────────────────

def test_a_storm_is_not_mailed_twice():
    """The nightly re-fetches the most recent days while their rolling maximum
    settles. Without this the same storm mails every night until someone turns
    the whole thing off."""
    _record('2026-06-10', [IN_AREA])
    m = _Mailer()
    assert alert.send_new(m, 'luke@example.com', today=dt.date(2026, 6, 11))['sent'] == 1
    assert alert.send_new(m, 'luke@example.com', today=dt.date(2026, 6, 11))['sent'] == 0
    assert len(m.sent) == 1


def test_a_failed_send_leaves_the_storm_pending():
    """Marking it sent on a failure is the one outcome nobody would notice:
    the storm is gone from the queue and was never delivered."""
    _record('2026-06-10', [IN_AREA])
    dead = _Mailer(ok=False)
    assert dead.ok is False
    assert alert.send_new(dead, 'luke@example.com', today=dt.date(2026, 6, 11))['sent'] == 0

    good = _Mailer()
    assert alert.send_new(good, 'luke@example.com', today=dt.date(2026, 6, 11))['sent'] == 1


def test_no_recipient_is_not_an_error_and_sends_nothing():
    _record('2026-06-10', [IN_AREA])
    assert alert.send_new(_Mailer(), '', today=dt.date(2026, 6, 11))['sent'] == 0


# ── the window ─────────────────────────────────────────────────────────

def test_a_backfilled_season_does_not_mail_three_years_of_history():
    """Filling the archive is exactly when this would fire hardest, and the
    one time it must not: those storms are months old and nobody is knocking
    them tomorrow."""
    for day in ('2024-07-21', '2024-08-07', '2026-06-10'):
        _record(day, [IN_AREA])
    m = _Mailer()
    out = alert.send_new(m, 'luke@example.com', today=dt.date(2026, 6, 11))
    assert out['sent'] == 1
    assert '2024' not in m.sent[0]['subject']


def test_a_future_dated_row_is_not_alerted_on():
    """The window needs BOTH ends. `storm_days(since=...)` has no ceiling of
    its own, so a floor alone is everything from that date onward."""
    _record('2026-06-20', [IN_AREA])
    assert alert.pending(today=dt.date(2026, 6, 11)) == []


# ── where a rep would go ───────────────────────────────────────────────

def test_a_cell_belongs_to_its_nearest_town_only():
    """A swath between two towns counted into both reads as twice the storm."""
    rects = [(40.39, -105.08, 40.40, -105.07, 1.5)]
    rows = alert.places(rects)
    assert len(rows) == 1
    assert rows[0][0] == 'Loveland'


def test_hail_far_from_any_town_still_gets_a_bearing():
    """"open county" is true and useless. The nearest town and a distance is
    something a rep can drive to."""
    rows = alert.places([(39.26, -102.97, 39.27, -102.96, 2.0)])
    assert rows[0][0].startswith('open county (nearest ')
    assert ' mi)' in rows[0][0]


def test_the_brief_says_mesh_is_an_estimate():
    """A rep will read this to a homeowner. MESH is the largest hail a storm
    could have produced, not a measurement of what landed on their roof."""
    _record('2026-06-10', [IN_AREA])
    evs = alert.pending(today=dt.date(2026, 6, 11))
    html = alert.brief_html(evs)
    assert 'estimate' in html.lower()
    assert 'not a number to quote a homeowner as fact' in html


# ── the service area is configurable ───────────────────────────────────

def test_the_service_area_can_be_moved_by_env(monkeypatch):
    monkeypatch.setenv('HAIL_ALERT_BOUNDS', '39.0,-103.5,39.5,-102.5')
    _record('2026-06-10', [FAR_AWAY])
    assert len(alert.pending(today=dt.date(2026, 6, 11))) == 1


def test_a_malformed_service_area_falls_back_rather_than_going_silent(monkeypatch):
    """A typo in a variable must not switch the alerts off with no sign."""
    monkeypatch.setenv('HAIL_ALERT_BOUNDS', 'not,a,box')
    assert alert.bounds() == alert.SERVICE_AREA
