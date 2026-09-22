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


def test_a_storm_elsewhere_in_colorado_still_alerts_but_says_so():
    """Luke asked for all of Colorado: a storm two counties over is where the
    next crew goes, so it is reported. The SUBJECT is what stops it reading as
    though it landed on the ground the crews work today."""
    _record('2026-06-10', [FAR_AWAY])
    m = _Mailer()
    out = alert.send_new(m, 'luke@example.com', today=dt.date(2026, 6, 11))
    assert out['sent'] == 1
    assert 'not our area' in m.sent[0]['subject']
    assert 'Elsewhere in Colorado' in m.sent[0]['html']
    assert 'In the service area' not in m.sent[0]['html']


def test_the_service_area_leads_the_subject_when_it_was_hit():
    """A brief that mixed them would bury six cells a crew can be on by
    breakfast beneath three hundred nobody is driving to."""
    _record('2026-06-10', [IN_AREA, FAR_AWAY])
    m = _Mailer()
    alert.send_new(m, 'luke@example.com', today=dt.date(2026, 6, 11))
    subject, html = m.sent[0]['subject'], m.sent[0]['html']
    assert 'in the service area' in subject
    assert '1.75' in subject, 'the subject quotes OUR worst, not the state\'s'
    assert html.index('In the service area') < html.index('Elsewhere in Colorado')


def test_the_statewide_half_can_be_switched_off(monkeypatch):
    monkeypatch.setenv('HAIL_ALERT_AREA_ONLY', '1')
    _record('2026-06-10', [FAR_AWAY])
    assert alert.send_new(_Mailer(), 'luke@example.com',
                          today=dt.date(2026, 6, 11))['sent'] == 0


def test_the_service_area_figures_never_include_cells_from_outside_it():
    """`max_size` is what landed on US. Letting the state's worst leak into it
    would overstate our own storm by whatever fell three counties away."""
    _record('2026-06-10', [IN_AREA, FAR_AWAY])
    evs = alert.pending(today=dt.date(2026, 6, 11))
    assert len(evs) == 1
    assert evs[0]['max_size'] == pytest.approx(1.75, abs=0.01)
    assert evs[0]['cells'] == 1
    assert evs[0]['away_max'] == pytest.approx(3.20, abs=0.01)
    assert evs[0]['away_cells'] == 1


def test_a_statewide_town_gets_named_so_the_brief_can_say_where():
    """Reporting the whole state is only useful if it can say where. A town
    list that stopped at the service area would answer every out-of-area storm
    with "open county, nearest Ault, 140 mi"."""
    rows = alert.places([(39.26, -102.97, 39.27, -102.96, 2.0)])
    assert rows[0][0] == 'Burlington' or rows[0][0].startswith('open county')
    assert alert.places([(38.83, -104.83, 38.84, -104.82, 2.0)])[0][0] == 'Colorado Springs'
    assert alert.places([(39.06, -108.56, 39.07, -108.55, 2.0)])[0][0] == 'Grand Junction' 


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
    assert rows[0][0].startswith('open county near ')
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


# ── the brief has to be readable ───────────────────────────────────────

def test_open_county_groups_by_town_not_by_distance():
    """Keying the bucket on the distance put 1,348 cells on the eastern plains
    into forty near-identical rows. The distance is something a row SHOWS,
    never something it is grouped by."""
    # Four cells, all well outside any town, at four different distances.
    rects = [(38.30 + i * 0.05, -102.80, 38.31 + i * 0.05, -102.79, 1.5 + i * 0.1)
             for i in range(4)]
    rows = alert.places(rects)
    assert len(rows) == 1, rows
    where, cells, mx = rows[0]
    assert cells == 4
    assert where.startswith('open county near ')
    assert 'mi)' in where


def test_a_section_caps_its_rows_and_says_it_did():
    """A statewide day is thousands of cells. The brief is a decision aid, not
    a dump — and a silent truncation is the thing `cells_in` already refuses."""
    rects = []
    for i, (town, (la, ln)) in enumerate(list(alert.TOWNS.items())[:12]):
        rects.append((la, ln, la + 0.01, ln + 0.01, 1.0 + i * 0.1))
    html = alert._section('Test', rects, limit=4)
    assert html.count('<tr>') == 4
    assert 'more place(s)' in html


def test_the_capped_rows_are_the_biggest_hail():
    """Dropping the tail is only honest if the tail is the small stuff."""
    rects = []
    for i, (town, (la, ln)) in enumerate(list(alert.TOWNS.items())[:6]):
        rects.append((la, ln, la + 0.01, ln + 0.01, 1.0 + i))
    html = alert._section('Test', rects, limit=2)
    assert '6.00' in html and '5.00' in html
    assert '1.00' not in html
