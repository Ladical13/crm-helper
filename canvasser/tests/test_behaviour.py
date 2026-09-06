"""What the canvasser actually does — pins, the leaderboard, the handoff.

This app had no behaviour tests at all: `test_assets.py` covers Leaflet
vendoring, the cache-buster and mobile CSS, which is why a dead reference
blanked the whole tool for three weeks without a build going red. These cover
the four things most likely to be wrong in a way nobody notices — each of them
produces a plausible screen rather than an error.
"""
import os
import sys
from datetime import datetime, timedelta

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)))))


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv('CANVASSER_DATA_DIR', str(tmp_path))
    monkeypatch.setenv('PORTAL_DATA_DIR', str(tmp_path))
    monkeypatch.setenv('SESSION_SECRET', 'test-secret')
    for mod in [m for m in list(sys.modules) if m in ('app', 'p1_canvass_app')]:
        del sys.modules[mod]
    import app as canvasser_app
    canvasser_app.app.config['TESTING'] = True
    with canvasser_app.app.test_client() as c:
        with c.session_transaction() as sess:
            sess['username'] = 'aaron'
            sess['is_admin'] = False
        yield c, canvasser_app


def _add_pin(mod, pin_id, rep, pin_type, days_ago=0, address=''):
    when = ((datetime.utcnow() - timedelta(days=days_ago))
            .strftime('%Y-%m-%dT%H:%M:%SZ'))
    with mod.get_db() as db:
        db.execute(
            'INSERT INTO pins (id, lat, lng, address, pin_type, rep, notes, '
            'contact_name, contact_phone, contact_email, crm_contact_id, '
            'crm_project_id, created_at, updated_at) '
            "VALUES (?,?,?,?,?,?,'','','','','','',?,?)",
            (pin_id, 40.58, -105.08, address, pin_type, rep, when, when))


# ── A doorstep tap is not a signed contract ─────────────────────────────────

def test_a_closed_door_does_not_write_a_won_lead(client):
    """Everywhere else in this system `won` means a customer signed — the
    estimator's funnel sets it on signature, and a signature outranks even a
    manual `lost`.

    Let a doorstep tap write `won` and it lands in the same bucket as a signed
    $28k roof. Close rate, revenue forecast and the leaderboard all inflate,
    and afterwards nobody can tell an asserted win from an earned one.
    """
    _, mod = client
    assert mod.PIN_STAGE['closed'] != 'won'
    assert mod.PIN_STAGE['closed'] == 'inspected'
    assert 'won' not in mod.PIN_STAGE.values(), (
        'no pin type may claim a win the customer has not signed for')


def test_the_three_answered_pin_types_still_never_become_leads(client):
    """`not_home` has nobody to follow up with yet; `no_interest` and
    `no_soliciting` are answers."""
    _, mod = client
    for t in ('not_home', 'no_interest', 'no_soliciting'):
        assert t not in mod.PIN_STAGE


# ── The map must not lie about what it is showing ───────────────────────────

def test_pins_come_back_with_a_truncation_flag(client):
    """The old endpoint returned a bare array capped at 2000. Past the cap the
    map silently showed a subset, so a street worked hard last month came back
    looking unknocked and a rep knocked it again."""
    c, mod = client
    for i in range(5):
        _add_pin(mod, f'p{i}', 'aaron', 'not_home')

    body = c.get('/api/pins?limit=3').get_json()
    assert len(body['pins']) == 3
    assert body['truncated'] is True, 'the map must say it is showing a subset'

    body = c.get('/api/pins?limit=50').get_json()
    assert len(body['pins']) == 5
    assert body['truncated'] is False


def test_pins_are_windowed_by_date(client):
    """A door knocked two years ago tells a rep nothing about today's street."""
    c, mod = client
    _add_pin(mod, 'recent', 'aaron', 'interested', days_ago=3)
    _add_pin(mod, 'ancient', 'aaron', 'interested', days_ago=400)

    ids = [p['id'] for p in c.get('/api/pins').get_json()['pins']]
    assert ids == ['recent']

    ids = [p['id'] for p in c.get('/api/pins?days=0').get_json()['pins']]
    assert set(ids) == {'recent', 'ancient'}, 'days=0 means the whole archive'


def test_a_junk_window_falls_back_to_the_default_rather_than_500ing(client):
    c, mod = client
    _add_pin(mod, 'p1', 'aaron', 'interested')
    res = c.get('/api/pins?days=notanumber&limit=banana')
    assert res.status_code == 200
    assert res.get_json()['window_days'] == mod.PIN_WINDOW_DAYS


# ── The leaderboard has to be winnable, and hard to game ────────────────────

def test_the_leaderboard_is_windowed_so_last_season_cannot_own_it(client):
    """With no date filter, whoever knocked most last season is permanently
    first and a new hire can never move. A board nobody can win stops being a
    board in about three weeks."""
    c, mod = client
    for i in range(50):
        _add_pin(mod, f'old{i}', 'veteran', 'not_home', days_ago=200)
    _add_pin(mod, 'new1', 'rookie', 'appointment', days_ago=1)

    reps = c.get('/api/leaderboard').get_json()['reps']
    assert [r['rep'] for r in reps] == ['rookie'], \
        'a 200-day-old streak must not appear in a 7-day window'


def test_ranking_is_on_appointments_not_on_doors_tapped(client):
    """`total_doors` is the easiest number in the company to game — a rep can
    tap "Not Home" fifteen times walking down a sidewalk. Doors are the
    denominator of the job, not the score."""
    c, mod = client
    for i in range(30):
        _add_pin(mod, f'tap{i}', 'tapper', 'not_home')
    for i in range(3):
        _add_pin(mod, f'set{i}', 'closer', 'appointment')

    reps = c.get('/api/leaderboard').get_json()['reps']
    assert reps[0]['rep'] == 'closer'
    assert reps[0]['total_doors'] < reps[1]['total_doors'], (
        'the rep with FEWER doors but more appointments must still rank first')


def test_rates_divide_by_contacts_not_by_doors(client):
    """A "Not Home" is a walk, not a conversation. Dividing by it makes a rep
    who knocks empty streets look efficient."""
    c, mod = client
    _add_pin(mod, 'a', 'aaron', 'appointment')
    _add_pin(mod, 'b', 'aaron', 'interested')
    for i in range(8):
        _add_pin(mod, f'nh{i}', 'aaron', 'not_home')

    rep = c.get('/api/leaderboard').get_json()['reps'][0]
    assert rep['total_doors'] == 10
    assert rep['contacts'] == 2
    assert rep['contact_rate'] == pytest.approx(0.2)
    assert rep['set_rate'] == pytest.approx(0.5), '1 appointment from 2 contacts'


def test_a_rep_with_no_doors_does_not_divide_by_zero(client):
    c, mod = client
    _add_pin(mod, 'a', 'aaron', 'not_home')
    rep = c.get('/api/leaderboard').get_json()['reps'][0]
    assert rep['set_rate'] == 0.0 and rep['contact_rate'] == 0.0


# ── Off means off ───────────────────────────────────────────────────────────

def test_a_rep_can_remove_their_own_location_immediately(client):
    """The "Team Locations: Off" toggle used to gate only the pull, so a rep
    who switched it off stopped seeing teammates and kept broadcasting. Both
    halves matter: the browser stops pushing, and the position already stored
    has to go rather than sit on everyone else's map for the live window."""
    c, mod = client
    c.post('/api/location', json={'lat': 40.58, 'lng': -105.08})
    assert len(c.get('/api/team-locations').get_json()) == 1

    assert c.delete('/api/location').status_code == 200
    assert c.get('/api/team-locations').get_json() == []


def test_deleting_a_location_that_was_never_set_is_not_an_error(client):
    c, _ = client
    assert c.delete('/api/location').status_code == 200
