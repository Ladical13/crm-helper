"""The National Do Not Call Registry.

Residential numbers on the registry must not be cold called or texted, and the
list must be re-loaded every 31 days. Business lines are generally exempt, and
so is anyone with an existing business relationship: a customer for 18 months
after buying, an inquirer for 3 months after contacting us. Each of those is a
rule a rep cannot be expected to check by hand forty times a day.
"""
from datetime import timedelta

import app as appmod
from conftest import signup, new_lead


def _load(client, text):
    return client.post('/api/dnc-registry', json={'text': text})


def _queue_ids(client):
    q = client.get('/api/queue/today').get_json()
    return {d['lead_id'] for d in q['due']} | {n['id'] for n in q['new']}


def _cold(client, phone, lead_type='homeowner', ref='r1', **extra):
    client.post('/api/prospects/import', json={
        'rows': [dict({'company': 'X', 'first_name': 'Pat', 'phone': phone,
                       'city': 'Loveland', 'source_ref': ref}, **extra)],
        'lead_type': lead_type, 'source': 'storm'})
    with appmod.get_db() as db:
        return db.execute('SELECT id FROM leads ORDER BY created_at DESC LIMIT 1').fetchone()['id']


def test_any_ten_digit_format_loads_and_areas_are_reported(client):
    signup(client)
    r = _load(client, '970,5550101\n(303) 555-0102\n+1 970 555 0103\njunk\n').get_json()
    assert r['loaded'] == 3 and r['areas'] == ['303', '970']


def test_a_refresh_replaces_its_area_code_whole(client):
    signup(client)
    _load(client, '9705550101\n9705550102\n3035550109')
    _load(client, '9705550199')
    st = {a['area']: a['numbers'] for a in client.get('/api/dnc-registry').get_json()['areas']}
    assert st == {'970': 1, '303': 1}


def test_a_rep_cannot_load_the_registry(client):
    signup(client)
    signup(client, 'casey')
    assert _load(client, '9705550101').status_code == 403


def test_a_registered_homeowner_never_reaches_the_queue(client):
    signup(client)
    lid = _cold(client, '970-555-0101')
    assert lid in _queue_ids(client)
    _load(client, '9705550101')
    assert lid not in _queue_ids(client)
    assert client.get(f'/api/leads/{lid}').get_json()['dnc_registry'] is True


def test_business_numbers_are_not_blocked(client):
    signup(client)
    lid = _cold(client, '970-555-0101', lead_type='church')
    _load(client, '9705550101')
    assert lid in _queue_ids(client)
    assert client.get(f'/api/leads/{lid}').get_json()['dnc_registry'] is False


def test_a_recent_customer_is_exempt(client):
    signup(client)
    lead = new_lead(client, phone='970-555-0101')
    with appmod.get_db() as db:
        db.execute('UPDATE leads SET won_at=? WHERE id=?', (appmod._now(), lead['id']))
    _load(client, '9705550101')
    assert client.get(f'/api/leads/{lead["id"]}').get_json()['dnc_registry'] is False


def test_an_old_customer_is_not(client):
    signup(client)
    lead = new_lead(client, phone='970-555-0101')
    old = appmod._iso(appmod._now_dt() - timedelta(days=appmod.DNC_EBR_SALE_DAYS + 5))
    with appmod.get_db() as db:
        db.execute('UPDATE leads SET won_at=? WHERE id=?', (old, lead['id']))
    _load(client, '9705550101')
    assert client.get(f'/api/leads/{lead["id"]}').get_json()['dnc_registry'] is True


def test_someone_who_contacted_us_is_exempt_for_three_months(client):
    signup(client)
    lead = new_lead(client, phone='970-555-0101', source='website')
    _load(client, '9705550101')
    assert client.get(f'/api/leads/{lead["id"]}').get_json()['dnc_registry'] is False
    old = appmod._iso(appmod._now_dt() - timedelta(days=appmod.DNC_EBR_INQ_DAYS + 2))
    with appmod.get_db() as db:
        db.execute('UPDATE leads SET created_at=? WHERE id=?', (old, lead['id']))
    assert client.get(f'/api/leads/{lead["id"]}').get_json()['dnc_registry'] is True


def test_the_registry_goes_stale_after_31_days(client):
    signup(client)
    new_lead(client)
    _load(client, '9705550101')
    assert client.get('/api/dnc-registry').get_json()['stale'] is False
    with appmod.get_db() as db:
        db.execute('UPDATE dnc_registry SET loaded_at=?',
                   (appmod._iso(appmod._now_dt() - timedelta(days=32)),))
    assert client.get('/api/dnc-registry').get_json()['stale'] is True
