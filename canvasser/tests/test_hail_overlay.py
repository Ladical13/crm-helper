"""The map overlay reads the radar archive too.

The address lookup was wired to MESH; the map beside it still drew NOAA
spotter reports as `max(500, size * 800)`-metre circles — a damage footprint
that exists nowhere in the data, around points that are call-ins rather than
measurements. Two views of "where did it hail" that disagreed about both the
data and the geometry, on one screen.

The rectangles are the point: a cell is the only ground the radar actually
made a claim about.
"""
import os
import re
import sys
from datetime import datetime, timedelta

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
sys.path.insert(0, os.path.dirname(os.path.dirname(HERE)))

from hail import grid as hgrid    # noqa: E402
from hail import storms           # noqa: E402

APP_JS = open(os.path.join(HERE, '..', 'static', 'app.js'), encoding='utf-8').read()


def _code(src):
    """The source with `//` line comments removed.

    A test that forbids a construct must not be broken by the comment
    explaining why it is forbidden — and the comment above the rectangle is
    precisely the sentence naming the circle it replaced.
    """
    return '\n'.join(re.sub(r'(^|\s)//.*$', '', line) for line in src.splitlines())

LAT, LNG = 40.5853, -105.0844
BOX = 'south=40.5&west=-105.2&north=40.7&east=-104.9'


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
            sess['username'] = 'aaron'
            sess['is_admin'] = False
        yield c, canvasser_app
    storms.reset_cache()


def _days_ago(n):
    return (datetime.utcnow() - timedelta(days=n)).strftime('%Y-%m-%d')


def _record(date, cells):
    storms.record(date, hgrid.Swath({hgrid.cell_index(la, ln): size
                                     for (la, ln), size in cells.items()}))


def _no_network(mod, monkeypatch):
    monkeypatch.setattr(mod, '_fetch_hail_days',
                        lambda ds: pytest.fail('fell through to the NOAA scan'))


# ── The picker ──────────────────────────────────────────────────────────────

def test_the_picker_offers_the_storms_the_archive_holds(client):
    c, _ = client
    _record(_days_ago(30), {(LAT, LNG): 1.75})
    _record(_days_ago(10), {(LAT, LNG): 1.00})
    data = c.get('/api/hail/storms').get_json()
    assert data['count'] == 2
    assert [s['event_date'] for s in data['storms']] == \
        sorted([s['event_date'] for s in data['storms']], reverse=True)


def test_a_day_with_no_hail_is_not_offered_as_a_storm(client):
    """`record()` keeps it on purpose — that is what tells "no hail" apart from
    "never ingested" — but a row that draws nothing reads as a broken link."""
    c, _ = client
    _record(_days_ago(5), {})
    assert c.get('/api/hail/storms').get_json()['count'] == 0
    # The fact that the day was looked at is still held.
    assert _days_ago(5) in storms.ingested_dates()


# ── The cells ───────────────────────────────────────────────────────────────

def test_cells_come_back_as_rectangles_not_a_radius(client):
    """A cell is the only ground the radar made a claim about."""
    c, _ = client
    d = _days_ago(20)
    _record(d, {(LAT, LNG): 1.75})
    cell = c.get(f'/api/hail/cells?start={d}&{BOX}').get_json()['cells'][0]
    assert set(cell) == {'s', 'w', 'n', 'e', 'size'}
    assert cell['n'] > cell['s'] and cell['e'] > cell['w']
    assert cell['s'] <= LAT <= cell['n'] and cell['w'] <= LNG <= cell['e']


def test_a_cell_hit_twice_reports_its_WORST_day(client):
    """MESH is already a maximum over its own window. A mean would shave the
    peak off every multi-day range — the number that decides whether a
    neighbourhood is worth knocking."""
    c, _ = client
    _record(_days_ago(9), {(LAT, LNG): 1.00})
    _record(_days_ago(8), {(LAT, LNG): 2.25})
    data = c.get(f'/api/hail/cells?start={_days_ago(10)}&end={_days_ago(7)}&{BOX}').get_json()
    assert data['count'] == 1, 'one cell, not one per day'
    assert data['cells'][0]['size'] == 2.25


def test_the_viewport_is_what_gets_returned(client):
    c, _ = client
    d = _days_ago(3)
    _record(d, {(LAT, LNG): 1.75, (39.0, -104.8): 2.5})
    both = c.get(f'/api/hail/cells?start={d}').get_json()
    near = c.get(f'/api/hail/cells?start={d}&{BOX}').get_json()
    assert both['count'] == 2 and near['count'] == 1


def test_three_sides_of_a_box_is_not_a_box(client):
    """Guessing the fourth would silently return the wrong ground."""
    c, _ = client
    d = _days_ago(3)
    _record(d, {(LAT, LNG): 1.75, (39.0, -104.8): 2.5})
    partial = c.get(f'/api/hail/cells?start={d}&south=40.5&west=-105.2&north=40.7').get_json()
    assert partial['count'] == 2, 'an incomplete box must not filter'


def test_a_min_size_filter_drops_the_small_cells(client):
    c, _ = client
    d = _days_ago(4)
    _record(d, {(LAT, LNG): 0.75, (40.60, -105.10): 2.0})
    data = c.get(f'/api/hail/cells?start={d}&{BOX}&min_size=1').get_json()
    assert data['count'] == 1 and data['cells'][0]['size'] == 2.0


def test_truncation_keeps_the_biggest_hail_and_says_so(client):
    """An arbitrary slice would hide the cells a rep most needs behind ones
    they do not."""
    c, _ = client
    d = _days_ago(6)
    _record(d, {(40.50 + i * 0.01, -105.10): 0.5 + i * 0.1 for i in range(12)})
    data = c.get(f'/api/hail/cells?start={d}&south=40.4&west=-105.2'
                 f'&north=40.8&east=-104.9&limit=3').get_json()
    assert data['truncated'] is True
    assert data['count'] == 3
    sizes = sorted((x['size'] for x in data['cells']), reverse=True)
    assert sizes[0] == pytest.approx(1.6, abs=0.01), 'the largest cell was dropped'


def test_no_hail_with_coverage_is_not_the_same_as_no_coverage(client):
    """The distinction the whole feature turns on, again — a map that shows
    nothing has to say which of the two it means."""
    c, _ = client
    d = _days_ago(7)
    _record(d, {})
    covered = c.get(f'/api/hail/cells?start={d}&{BOX}').get_json()
    assert covered['count'] == 0 and covered['days_held'] == 1

    empty = c.get(f'/api/hail/cells?start=2021-01-01&end=2021-01-02&{BOX}').get_json()
    assert empty['count'] == 0 and empty['days_held'] == 0


def test_a_request_with_no_date_is_refused(client):
    c, _ = client
    assert c.get('/api/hail/cells').status_code == 400


def test_the_archive_routes_never_touch_noaa(client, monkeypatch):
    c, mod = client
    _no_network(mod, monkeypatch)
    d = _days_ago(2)
    _record(d, {(LAT, LNG): 1.5})
    assert c.get('/api/hail/storms').status_code == 200
    assert c.get(f'/api/hail/cells?start={d}&{BOX}').status_code == 200


# ── The front end ───────────────────────────────────────────────────────────

def test_the_overlay_draws_rectangles_not_invented_circles():
    draw = _code(APP_JS[APP_JS.index('function drawHailCells'):
                        APP_JS.index('async function loadSpcReports')])
    assert 'L.rectangle' in draw
    assert 'L.circle' not in draw
    assert '800' not in draw, 'the invented damage footprint came back'


def test_the_spotter_fallback_survives_and_is_labelled_as_call_ins():
    """It is the answer for dates the archive has not been backfilled over, and
    it must never be mistaken for a measurement of that spot."""
    spc = APP_JS[APP_JS.index('async function loadSpcReports'):]
    spc = spc[:spc.index("$('clear-hail-btn')")]
    assert 'L.circle' in spc, 'the fallback stopped drawing anything'
    assert 'Spotter report' in spc
    assert 'not a measurement' in spc


def test_the_overlay_asks_the_archive_before_the_spotter_reports():
    handler = APP_JS[APP_JS.index("$('load-hail-btn')"):]
    handler = handler[:handler.index('function drawHailCells')]
    assert handler.index('/api/hail/cells') < handler.index('loadSpcReports')


def test_the_viewport_is_sent_with_the_request():
    handler = APP_JS[APP_JS.index("$('load-hail-btn')"):]
    handler = handler[:handler.index('function drawHailCells')]
    assert 'map.getBounds()' in handler
    for side in ('south=', 'west=', 'north=', 'east='):
        assert side in handler


def test_truncation_is_said_out_loud():
    draw = APP_JS[APP_JS.index('function drawHailCells'):]
    draw = draw[:draw.index('async function loadSpcReports')]
    assert 'data.truncated' in draw
    assert 'zoom in' in draw
