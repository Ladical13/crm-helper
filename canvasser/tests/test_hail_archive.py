"""Hail by Address reads the radar archive, and says which product answered.

`hail/storms.py` was written to replace this endpoint — `history_at`'s own
docstring says so in as many words — and then nothing pointed at it, so the
archive filled up while the tool the reps hold went on scanning five years of
NOAA spotter CSVs. These tests hold the wiring in place, and hold down the one
distinction the whole feature rests on: "radar looked at this roof and saw no
hail" and "nobody ever ingested that period" are different answers, and only
one of them is safe to repeat to a homeowner.
"""
import os
import sys
from datetime import datetime, timedelta

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)))))

from hail import grid as hgrid      # noqa: E402
from hail import storms             # noqa: E402
from portal import geo as pgeo      # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
APP_JS = open(os.path.join(HERE, '..', 'static', 'app.js'), encoding='utf-8').read()


def _code(src):
    """The source with `//` line comments stripped.

    A test that requires a phrase must not be satisfied by the comment
    explaining it — and these comments quote the very sentences under test.
    """
    import re as _re
    return '\n'.join(_re.sub(r'(^|\s)//.*$', '', ln) for ln in src.splitlines())


# A house in Fort Collins, and a point far enough away to land in another cell.
LAT, LNG = 40.5853, -105.0844


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv('CANVASSER_DATA_DIR', str(tmp_path))
    monkeypatch.setenv('PORTAL_DATA_DIR', str(tmp_path))
    monkeypatch.setenv('HAIL_DATA_DIR', str(tmp_path))
    monkeypatch.setenv('SESSION_SECRET', 'test-secret')
    storms.reset_cache()
    pgeo.reset_cache()
    for mod in [m for m in list(sys.modules) if m in ('app', 'p1_canvass_app')]:
        del sys.modules[mod]
    import app as canvasser_app
    canvasser_app.app.config['TESTING'] = True
    from portal import users as pusers
    for username in ('aaron', 'bryan'):
        if not pusers.get(username):
            pusers.create(username, password='test-only', role='rep')
    with canvasser_app.app.test_client() as c:
        with c.session_transaction() as sess:
            sess['username'] = 'aaron'
            sess['is_admin'] = False
        yield c, canvasser_app
    storms.reset_cache()
    pgeo.reset_cache()


def _days_ago(n):
    return (datetime.utcnow() - timedelta(days=n)).strftime('%Y-%m-%d')


def _record(date, cells):
    """Store one storm day. `cells` is {(lat, lng): size_in}; an empty dict is
    a day that was looked at and had no qualifying hail, which is a fact the
    archive keeps on purpose."""
    swath = hgrid.Swath({hgrid.cell_index(la, ln): size
                         for (la, ln), size in cells.items()})
    storms.record(date, swath)


def _no_network(mod, monkeypatch):
    """Any fall-through to NOAA in these tests is a failure, not a slow test."""
    def boom(*a, **k):
        raise AssertionError('fell through to the NOAA CSV scan')
    monkeypatch.setattr(mod, '_fetch_hail_days', boom)


# ── The archive answers, and it answers about the roof ──────────────────────

def test_a_lookup_reads_the_radar_archive_not_the_spotter_reports(client, monkeypatch):
    c, mod = client
    _no_network(mod, monkeypatch)
    _record(_days_ago(30), {(LAT, LNG): 1.75})

    r = c.get(f'/api/hail/address?lat={LAT}&lng={LNG}')
    assert r.status_code == 200
    data = r.get_json()
    assert data['source'] == 'mrms_mesh'
    assert data['max_size'] == 1.75
    assert data['storm_count'] == 1


def test_the_reported_size_is_this_roofs_cell_not_a_neighbours(client, monkeypatch):
    """MESH's whole claim over SPC is that it is about this ground. A cell a
    few hundred metres away getting shelled says nothing about this roof, and
    reporting it would rebuild the very overclaim the archive replaced."""
    c, mod = client
    _no_network(mod, monkeypatch)
    _record(_days_ago(10), {(LAT + 0.05, LNG + 0.05): 2.75})

    data = c.get(f'/api/hail/address?lat={LAT}&lng={LNG}').get_json()
    assert data['source'] == 'mrms_mesh'
    assert data['storm_count'] == 0
    assert data['max_size'] == 0


def test_storms_come_back_newest_first_with_the_worst_size_available(client, monkeypatch):
    c, mod = client
    _no_network(mod, monkeypatch)
    _record(_days_ago(400), {(LAT, LNG): 2.25})
    _record(_days_ago(20), {(LAT, LNG): 1.00})

    data = c.get(f'/api/hail/address?lat={LAT}&lng={LNG}').get_json()
    dates = [s['date'] for s in data['storms']]
    assert dates == sorted(dates, reverse=True)
    # The headline number is the worst hail the roof ever took, not the latest.
    assert data['max_size'] == 2.25


# ── The distinction the feature rests on ────────────────────────────────────

def test_no_hail_over_the_roof_is_not_the_same_answer_as_no_archive(client, monkeypatch):
    """The trap this endpoint is most likely to fall into.

    A day with no qualifying hail is still recorded (`storms.record`), so an
    archive that has been ingested and found nothing must answer "no hail",
    with its coverage attached — while an archive nobody has filled must NOT,
    because "your roof is clean" said on the strength of an empty database is
    a sentence a rep repeats on a doorstep.
    """
    c, mod = client
    _no_network(mod, monkeypatch)
    _record(_days_ago(5), {})           # looked, saw nothing
    _record(_days_ago(6), {})

    data = c.get(f'/api/hail/address?lat={LAT}&lng={LNG}').get_json()
    assert data['source'] == 'mrms_mesh'
    assert data['storm_count'] == 0
    assert data['coverage']['days_held'] == 2


def test_an_empty_archive_falls_back_to_the_spotter_reports(client, monkeypatch):
    """Nothing ingested at all: degrade to the old answer, and label it."""
    c, mod = client
    monkeypatch.setattr(mod, '_fetch_hail_days', lambda ds: {})

    data = c.get(f'/api/hail/address?lat={LAT}&lng={LNG}').get_json()
    assert data['source'] == 'noaa_spc'
    assert data['archive_empty'] is True


def test_an_archive_that_stops_short_of_the_window_still_answers(client, monkeypatch):
    """Partial coverage is the normal state of a backfill in progress. It is a
    MESH answer about the days actually held, and `coverage` is what stops that
    being read as five clean years."""
    c, mod = client
    _no_network(mod, monkeypatch)
    _record(_days_ago(30), {(LAT, LNG): 1.5})

    data = c.get(f'/api/hail/address?lat={LAT}&lng={LNG}&days=1825').get_json()
    assert data['source'] == 'mrms_mesh'
    assert data['coverage']['days_held'] == 1
    assert data['lookback_days'] == 1825


def test_a_storm_outside_the_lookback_window_is_not_reported(client, monkeypatch):
    c, mod = client
    monkeypatch.setattr(mod, '_fetch_hail_days', lambda ds: {})
    _record(_days_ago(800), {(LAT, LNG): 2.0})

    data = c.get(f'/api/hail/address?lat={LAT}&lng={LNG}&days=90').get_json()
    # Nothing is held inside 90 days, so this is an archive gap, not "no hail".
    assert data['source'] == 'noaa_spc'


def test_min_size_filters_the_history(client, monkeypatch):
    c, mod = client
    _no_network(mod, monkeypatch)
    _record(_days_ago(9), {(LAT, LNG): 0.75})
    _record(_days_ago(8), {(LAT, LNG): 1.50})

    data = c.get(f'/api/hail/address?lat={LAT}&lng={LNG}&min_size=1').get_json()
    assert [s['size'] for s in data['storms']] == [1.5]
    # The day the small hail fell is still counted as covered.
    assert data['coverage']['days_held'] == 2


# ── Geocoding ───────────────────────────────────────────────────────────────

def test_a_cached_address_never_reaches_the_geocoder(client, monkeypatch):
    """Every pin drop and every search in this app geocodes, from one Railway
    IP, against a service whose policy is one request a second and no bulk use.
    The cache is what keeps the second rep on the same street off that path."""
    c, mod = client
    _no_network(mod, monkeypatch)
    _record(_days_ago(3), {(LAT, LNG): 1.25})
    pgeo.put('1420 Oak St, Fort Collins CO', lat=LAT, lng=LNG,
             matched='1420 Oak St', source='census')

    def boom(*a, **k):
        raise AssertionError('hit Nominatim for an address already cached')
    monkeypatch.setattr(mod.http, 'get', boom)

    data = c.get('/api/hail/address?q=1420+Oak+St,+Fort+Collins+CO').get_json()
    assert data['source'] == 'mrms_mesh'
    assert data['max_size'] == 1.25
    assert data['resolved'] == '1420 Oak St'


def test_a_geocoded_address_is_written_back_to_the_shared_cache(client, monkeypatch):
    c, mod = client
    _no_network(mod, monkeypatch)
    _record(_days_ago(3), {(LAT, LNG): 1.25})

    class FakeResp:
        def raise_for_status(self): pass
        def json(self): return [{'lat': str(LAT), 'lon': str(LNG),
                                 'display_name': '9 Elm St, Greeley, CO'}]
    monkeypatch.setattr(mod.http, 'get', lambda *a, **k: FakeResp())

    c.get('/api/hail/address?q=9+Elm+St+Greeley+CO')
    assert pgeo.lookup('9 Elm St Greeley CO') is not None


def test_an_address_that_cannot_be_resolved_is_a_404(client, monkeypatch):
    c, mod = client
    class FakeResp:
        def raise_for_status(self): pass
        def json(self): return []
    monkeypatch.setattr(mod.http, 'get', lambda *a, **k: FakeResp())
    assert c.get('/api/hail/address?q=nowhere+at+all').status_code == 404


def test_a_junk_lookback_falls_back_rather_than_500ing(client, monkeypatch):
    c, mod = client
    _no_network(mod, monkeypatch)
    _record(_days_ago(3), {(LAT, LNG): 1.25})
    r = c.get(f'/api/hail/address?lat={LAT}&lng={LNG}&days=abc&min_size=xyz')
    assert r.status_code == 200
    assert r.get_json()['source'] == 'mrms_mesh'


# ── A thin archive must not answer as a confident negative ─────────────
#
# The bug that sent this whole investigation: a five-year lookup on a real
# Loveland address returned "No hail on record" in headline type, off two days
# of coverage, with the caveat in a grey footnote. MESH holds a 1.91" storm
# over that town on 2024-07-21. The tool was not wrong about the data — it had
# almost none, and said so in the one place nobody reads.

_MESH_RENDER = APP_JS[APP_JS.index('function renderMeshHistory'):
                      APP_JS.index('function renderSpcReports')]


def test_the_empty_answer_is_gated_on_how_much_archive_there_is():
    """"No hail on record" is only honest when the record covers the question.

    The server returns this shape when the archive holds SOME day in the
    window, which is not the same as holding the window.
    """
    code = _code(_MESH_RENDER)
    assert 'cov.status' in code, (
        'renderMeshHistory does not look at how much was ASKED for, so it '
        'cannot tell a covered window from an almost-empty one')
    assert 'coverage' in code
    assert 'is-thin' in code, 'no distinct treatment for a thin archive'


def test_the_thin_answer_does_not_claim_the_roof_was_never_hit():
    thin = _MESH_RENDER[_MESH_RENDER.index('is-thin'):]
    thin = thin[:thin.index('`  :  `') if '`  :  `' in thin else len(thin)]
    assert 'Not enough radar history' in _MESH_RENDER
    assert 'does not establish' in _MESH_RENDER, (
        'the thin branch must say what it is NOT claiming')


def test_the_coverage_is_still_shown_when_the_archive_is_good():
    """The honest negative stays available — this is not "never say no hail".
    A five-year archive that saw nothing is a real and useful answer."""
    assert 'No archived hail' in _MESH_RENDER
    assert 'hailCoverageText(cov)' in _MESH_RENDER


# ── A number a rep reads out loud ──────────────────────────────────────

def test_an_impossible_reading_reaches_the_rep_with_its_caveat(client, monkeypatch):
    """MESH is what a storm could have produced aloft, not what landed. The
    archive holds cells above the largest hailstone ever recorded in the US,
    and the rep holding the phone is the last person who can catch that."""
    c, mod = client
    _no_network(mod, monkeypatch)
    _record(_days_ago(5), {(LAT, LNG): 8.85})

    data = c.get(f'/api/hail/address?lat={LAT}&lng={LNG}').get_json()
    assert data['source'] == 'mrms_mesh'
    assert data['max_size'] == pytest.approx(8.85, abs=0.01), 'the value is passed through'
    assert data['max_caveat'] == 'impossible'
    assert 'do not quote' in data['max_note']
    assert data['storms'][0]['caveat'] == 'impossible'


def test_ordinary_hail_carries_no_caveat_to_the_rep(client, monkeypatch):
    c, mod = client
    _no_network(mod, monkeypatch)
    _record(_days_ago(5), {(LAT, LNG): 1.75})
    data = c.get(f'/api/hail/address?lat={LAT}&lng={LNG}').get_json()
    assert data['max_caveat'] == ''
    assert data['max_note'] == ''


def test_the_screen_shows_the_caveat_beside_the_number():
    """In a doc nobody opens is not showing it.

    Checking that the note is BUILT is not enough — deleting the one
    interpolation that puts it on screen left this passing while the caveat
    rendered nowhere. It has to appear inside the template that becomes
    innerHTML, after the number it qualifies.
    """
    code = _code(_MESH_RENDER)
    assert 'max_note' in code, 'the note is never built'
    assert 'hail-caveat' in code, 'no styled element for it'
    body = code[code.index('is-headline'):]
    assert '${note}' in body, (
        'the caveat is computed and then never interpolated into the markup')
    assert body.index('hail-summary-sub') < body.index('${note}'), (
        'the caveat must sit with the figure it qualifies')
