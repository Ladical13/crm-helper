"""Storms joined to the pipeline.

The rules worth pinning: a lead under a 1"+ storm gets exactly one follow-up
per storm, past customers are worked first, anyone who said no (or is on the
Do Not Call registry) is left alone, and a lead we cannot place is COUNTED as
unchecked — never silently read as "no hail".
"""
import app as appmod
from conftest import signup, new_lead

from hail import grid as hgrid, storms
from portal import geo

FOCO = (40.5853, -105.0844)


def _ago(days):
    """A storm date relative to today, so the tests that look back a year
    do not quietly expire."""
    return (appmod._now_dt() - __import__('datetime').timedelta(days=days)).strftime('%Y-%m-%d')

LOVE = (40.3978, -105.0750)


def _place(lead, lat, lng):
    geo.put(f"{lead['address']} {lead['city']} CO", lat, lng)


def _storm(date, *points):
    return storms.record(date, hgrid.swath_from_points(points, threshold_in=0.75))


def _lead(client, addr, **kw):
    return new_lead(client, address=addr, city='Fort Collins', state='CO', **kw)


def _tasks(lead_id):
    with appmod.get_db() as db:
        return [dict(r) for r in db.execute(
            "SELECT * FROM tasks WHERE lead_id=? AND done=0 AND title LIKE 'Storm:%'", (lead_id,))]


def test_a_lead_under_the_storm_gets_one_follow_up(client):
    signup(client)
    a = _lead(client, '1 Hail Way')
    _place(a, *FOCO)
    ev = _storm('2026-06-01', (*FOCO, 1.5))
    r = appmod.storm_queue(ev['event_id'])
    assert r['queued'] == 1
    assert _tasks(a['id'])[0]['title'] == 'Storm: 1.50" hail on 2026-06-01'
    again = appmod.storm_queue(ev['event_id'])
    assert (again['queued'], again['already_queued']) == (0, 1)
    assert len(_tasks(a['id'])) == 1


def test_past_customers_are_worked_first(client):
    signup(client)
    cold = _lead(client, '2 Hail Way')
    cust = _lead(client, '3 Hail Way')
    client.patch(f'/api/leads/{cust["id"]}/stage', json={'stage': 'won'})
    for l in (cold, cust):
        _place(l, *FOCO)
    ev = _storm('2026-06-02', (*FOCO, 1.25))
    r = appmod.storm_queue(ev['event_id'])
    assert r['by_tier'] == {'cold': 1, 'past_customer': 1}
    assert _tasks(cust['id'])[0]['due_at'] < _tasks(cold['id'])[0]['due_at']


def test_hail_under_an_inch_books_nothing(client):
    signup(client)
    a = _lead(client, '4 Hail Way')
    _place(a, *FOCO)
    ev = _storm('2026-06-03', (*FOCO, 0.8))
    assert appmod.storm_queue(ev['event_id'])['queued'] == 0


def test_a_lead_outside_the_swath_books_nothing(client):
    signup(client)
    a = _lead(client, '5 Hail Way')
    _place(a, *LOVE)
    ev = _storm('2026-06-04', (*FOCO, 2.0))
    assert appmod.storm_queue(ev['event_id'])['queued'] == 0


def test_someone_who_said_no_is_left_alone(client):
    signup(client)
    a = _lead(client, '6 Hail Way')
    _place(a, *FOCO)
    client.post(f'/api/leads/{a["id"]}/outcome', json={'outcome': 'not_interested'})
    ev = _storm('2026-06-05', (*FOCO, 1.75))
    assert appmod.storm_queue(ev['event_id'])['queued'] == 0


def test_a_do_not_call_homeowner_is_left_alone(client):
    signup(client)
    a = _lead(client, '7 Hail Way', phone='970-555-0177')
    _place(a, *FOCO)
    client.post('/api/dnc-registry', json={'text': '9705550177'})
    ev = _storm('2026-06-06', (*FOCO, 1.75))
    assert appmod.storm_queue(ev['event_id'])['queued'] == 0


def test_a_lead_we_cannot_place_is_counted_not_dropped(client):
    signup(client)
    _lead(client, '8 Nowhere Rd')                          # never geocoded
    ev = _storm('2026-06-07', (*FOCO, 1.75))
    assert appmod.storm_queue(ev['event_id'])['no_coords'] >= 1


def test_tagging_fills_the_storm_line_the_templates_use(client):
    signup(client)
    hit = _lead(client, '9 Hail Way', first_name='Dana')
    small = _lead(client, '10 Hail Way')
    # Coordinates of its own: the hail archive outlives each test, and this
    # asks for the WORST hail of the year at the spot.
    here, there = (40.4801, -104.9012), (40.4302, -104.8013)
    _place(hit, *here)
    _place(small, *there)
    when = _ago(30)
    _storm(when, (*here, 1.25), (*there, 0.8))
    r = appmod.storm_tag_leads()
    assert r['tagged'] >= 1
    with appmod.get_db() as db:
        line = db.execute('SELECT recent_storm FROM leads WHERE id=?', (hit['id'],)).fetchone()[0]
        small_line = db.execute('SELECT recent_storm FROM leads WHERE id=?', (small['id'],)).fetchone()[0]
    assert '1.25-inch hail' in line
    d = __import__('datetime').datetime.strptime(when, '%Y-%m-%d')
    assert f'{d.strftime("%B")} {d.day}, {d.year}' in line
    assert small_line == ''
    email = client.get(f'/api/leads/{hit["id"]}/draft').get_json()
    assert '1.25-inch hail' in email['body']


def test_the_storm_list_reports_what_was_queued(client):
    signup(client)
    a = _lead(client, '11 Hail Way')
    _place(a, *FOCO)
    ev = _storm(_ago(10), (*FOCO, 1.5))
    client.post(f'/api/storms/{ev["event_id"]}/queue')
    evs = {e['event_id']: e for e in client.get('/api/storms?days=60').get_json()['events']}
    assert evs[ev['event_id']]['queued'] == 1


def test_a_rep_cannot_trigger_a_storm_queue(client):
    signup(client)
    ev = _storm('2026-06-10', (*FOCO, 1.5))
    signup(client, 'casey')
    assert client.post(f'/api/storms/{ev["event_id"]}/queue').status_code == 403
