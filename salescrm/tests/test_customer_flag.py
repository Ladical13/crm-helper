"""Customers we would rather not work for again.

Two levels, because they behave differently. `caution` warns the rep and
changes nothing else — a difficult customer can still be a job worth doing, and
burying that in a suppression list takes the decision away from the person best
placed to make it. `do_not_serve` actually stops things happening.

Distinct from `leads.dnc`, which is the customer's choice not to hear from us.
This one is ours.
"""
import pytest
from conftest import signup, login, new_lead
import app as appmod
from hail import grid as hgrid, storms as hstorms
from portal import geo as pgeo

FOCO = (40.5853, -105.0844)


@pytest.fixture(autouse=True)
def storm_db(tmp_path, monkeypatch):
    monkeypatch.setenv('HAIL_DATA_DIR', str(tmp_path))
    hstorms.reset_cache(); pgeo.reset_cache()
    yield
    hstorms.reset_cache(); pgeo.reset_cache()


def _cust_id(client, lead):
    return client.get(f'/api/leads/{lead["id"]}').get_json()['customer']['id']


def _flag(client, cid, flag, reason='They threatened the crew'):
    return client.put(f'/api/customers/{cid}',
                      json={'flag': flag, 'flag_reason': reason})


def _placed(client, address='12 Elm St', **kw):
    lead = new_lead(client, address=address, city='Fort Collins', state='CO',
                    last_name='Reed', **kw)
    pgeo.put(lead['address'], lat=FOCO[0], lng=FOCO[1], matched=lead['address'],
             source='census', status='ok',
             key=pgeo.norm_address(lead['address'], lead['city'],
                                   lead['state'], lead['zip']))
    return lead


def test_a_flag_is_recorded_with_who_and_when(client):
    """An unattributed "difficult customer" is a rumour; one with a name and a
    date is information."""
    signup(client)
    lead = new_lead(client, last_name='Reed', phone='9705551212')
    cid = _cust_id(client, lead)
    _flag(client, cid, 'caution')
    got = client.get(f'/api/customers/{cid}').get_json()
    assert got['flag'] == 'caution'
    assert got['flag_by'] == 'luke' and got['flag_at']
    assert got['flag_reason'] == 'They threatened the crew'


def test_the_flag_reaches_the_rep_on_the_lead(client):
    """Before they pick up the phone, not after."""
    signup(client)
    lead = new_lead(client, last_name='Reed', phone='9705551212')
    _flag(client, _cust_id(client, lead), 'do_not_serve', reason='Never again')
    c = client.get(f'/api/leads/{lead["id"]}').get_json()['customer']
    assert c['flag'] == 'do_not_serve'
    assert 'Do not work with again' in c['flag_label']
    assert c['flag_reason'] == 'Never again'


def test_the_flag_follows_the_person_across_every_deal(client):
    """The whole reason it lives on the customer and not the lead."""
    signup(client)
    roof = new_lead(client, last_name='Reed', phone='9705551212')
    _flag(client, _cust_id(client, roof), 'caution')
    siding = new_lead(client, last_name='Reed', phone='9705551212',
                      service='window_cleaning')
    assert client.get(f'/api/leads/{siding["id"]}').get_json()['customer']['flag'] == 'caution'


def test_an_unknown_flag_is_refused(client):
    signup(client)
    lead = new_lead(client, last_name='Reed', phone='9705551212')
    assert _flag(client, _cust_id(client, lead), 'blacklisted').status_code == 400


def test_clearing_the_flag_clears_the_attribution(client):
    signup(client)
    lead = new_lead(client, last_name='Reed', phone='9705551212')
    cid = _cust_id(client, lead)
    _flag(client, cid, 'caution')
    _flag(client, cid, '', reason='')
    got = client.get(f'/api/customers/{cid}').get_json()
    assert got['flag'] == '' and got['flag_by'] == ''


# ── What each level actually does ────────────────────────────────────────────

def test_do_not_serve_is_left_out_of_storm_alerts(client):
    signup(client)
    lead = _placed(client, phone='9705551212')
    client.patch(f'/api/leads/{lead["id"]}/stage', json={'stage': 'won'})
    _flag(client, _cust_id(client, lead), 'do_not_serve')
    eid = hstorms.record('2026-06-12',
                         hgrid.swath_from_points([(*FOCO, 1.75)], threshold_in=1.0),
                         source='mrms_mesh')['event_id']
    got = client.get(f'/api/storm/{eid}').get_json()
    assert all(not v for v in got['by_tier'].values())


def test_caution_still_appears_in_storm_alerts(client):
    """A difficult customer may still be a good job. The rep decides, warned."""
    signup(client)
    lead = _placed(client, phone='9705551212')
    client.patch(f'/api/leads/{lead["id"]}/stage', json={'stage': 'won'})
    _flag(client, _cust_id(client, lead), 'caution')
    eid = hstorms.record('2026-06-12',
                         hgrid.swath_from_points([(*FOCO, 1.75)], threshold_in=1.0),
                         source='mrms_mesh')['event_id']
    got = client.get(f'/api/storm/{eid}').get_json()
    assert [h['id'] for h in got['by_tier']['past_customer']] == [lead['id']]


def test_do_not_serve_drops_out_of_the_outreach_queue(client):
    signup(client)
    lead = new_lead(client, last_name='Reed', phone='9705551212')
    _flag(client, _cust_id(client, lead), 'do_not_serve')
    q = client.get('/api/queue/today').get_json()
    assert lead['id'] not in [x['id'] for x in q['new']]
    assert lead['id'] not in [x['lead_id'] for x in q['due']]


def test_caution_stays_in_the_outreach_queue(client):
    signup(client)
    lead = new_lead(client, last_name='Reed', phone='9705551212')
    _flag(client, _cust_id(client, lead), 'caution')
    q = client.get('/api/queue/today').get_json()
    assert lead['id'] in [x['id'] for x in q['new']] + [x['lead_id'] for x in q['due']]
