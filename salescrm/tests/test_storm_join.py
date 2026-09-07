"""The swath against our own people — the join that had no caller.

hail/join.affected() is the module CLAUDE.md calls the reason for building any
of the storm work: a hail map is a commodity, and what no vendor can sell us is
this. It was finished and tested and nothing in the repo called it. Neither did
portal/geo.py outside its own backfill. Three complete pieces, no wire.

These exercise the wire against a synthetic swath, so they need no network and
no MRMS ingest.
"""
import pytest
from conftest import signup, login, new_lead

import app as appmod
from hail import grid as hgrid, storms as hstorms
from portal import geo as pgeo

FOCO = (40.5853, -105.0844)
GREELEY = (40.4233, -104.7091)


@pytest.fixture(autouse=True)
def storm_db(tmp_path, monkeypatch):
    monkeypatch.setenv('HAIL_DATA_DIR', str(tmp_path))
    hstorms.reset_cache()
    pgeo.reset_cache()
    yield
    hstorms.reset_cache()
    pgeo.reset_cache()


def _storm(*points, date='2026-06-12', source='mrms_mesh'):
    swath = hgrid.swath_from_points(points, threshold_in=1.0)
    return hstorms.record(date, swath, source=source)['event_id']


def _place(lead, at):
    """Put a lead's address in the geocode cache exactly as the backfill does.

    All four parts, because the KEY is built from all four. Seeding on the
    street alone produced a different key and the join found nobody -- which is
    the silent failure geo.norm_address warns about in its own docstring: it
    reports zero customers under a swath and reads exactly like a quiet storm.
    """
    pgeo.put(lead['address'], lat=at[0], lng=at[1], matched=lead['address'],
             source='census', status='ok',
             key=pgeo.norm_address(lead['address'], lead['city'],
                                   lead['state'], lead['zip']))


def _lead_at(client, address, at, **kw):
    lead = new_lead(client, address=address, city='Fort Collins', state='CO', **kw)
    _place(lead, at)
    return lead


def test_a_customer_under_the_swath_is_found(client):
    signup(client)
    lead = _lead_at(client, '12 Elm St', FOCO)
    eid = _storm((*FOCO, 1.75))
    got = client.get(f'/api/storm/{eid}').get_json()
    assert [h['id'] for h in got['by_tier']['open_lead']] == [lead['id']]
    assert got['by_tier']['open_lead'][0]['hail_size_in'] == pytest.approx(1.75)


def test_a_customer_outside_it_is_not(client):
    signup(client)
    _lead_at(client, '9 Ash Ave', GREELEY)
    got = client.get(f'/api/storm/{_storm((*FOCO, 1.75))}').get_json()
    assert all(not v for v in got['by_tier'].values())


def test_customers_we_cannot_place_are_COUNTED(client):
    """join.affected()'s own contract: dropping un-geocoded customers silently
    is how a brief says "40 affected" when the truth is 400 — which reads as a
    small storm, and nobody investigates a small storm."""
    signup(client)
    _lead_at(client, '12 Elm St', FOCO)
    new_lead(client, address='404 Nowhere Rd', city='Fort Collins')   # not geocoded
    got = client.get(f'/api/storm/{_storm((*FOCO, 1.75))}').get_json()
    assert got['unplaced'] == 1
    assert got['placed'] == 1


def test_the_lookup_key_matches_what_the_backfill_writes(client):
    """The two halves of the join build the address key independently, and a
    mismatch is invisible: no error, no log, just a storm that appears to have
    missed everybody."""
    from portal import geocode_backfill as backfill
    signup(client)
    lead = _lead_at(client, '12 Elm St', FOCO)
    written = backfill.crm_addresses()[0]
    assert pgeo.norm_address(*written[0]) == pgeo.norm_address(
        lead['address'], lead['city'], lead['state'], lead['zip'])
    assert pgeo.lookup(*written[0]) is not None


def test_a_lead_with_no_address_at_all_is_not_counted_as_placed(client):
    signup(client)
    new_lead(client, address='')
    got = client.get(f'/api/storm/{_storm((*FOCO, 1.75))}').get_json()
    assert got['placed'] == 0


# ── Tiers are a business rule, not a sort key ────────────────────────────────

def test_a_plan_subscriber_outranks_a_past_customer(client):
    """An RCP subscriber is a contractual obligation and is contacted first,
    always — even when a past customer took bigger hail."""
    signup(client)
    sub = _lead_at(client, '1 Plan St', FOCO, plan='roof_care', billing='annual')
    past = _lead_at(client, '2 Past St', FOCO)
    client.patch(f'/api/leads/{sub["id"]}/stage', json={'stage': 'won'})
    client.patch(f'/api/leads/{past["id"]}/stage', json={'stage': 'won'})
    got = client.get(f'/api/storm/{_storm((*FOCO, 1.75))}').get_json()
    assert [h['id'] for h in got['by_tier']['rcp']] == [sub['id']]
    assert [h['id'] for h in got['by_tier']['past_customer']] == [past['id']]


def test_a_lost_deal_is_its_own_tier(client):
    signup(client)
    lead = _lead_at(client, '3 Lost Ln', FOCO)
    client.patch(f'/api/leads/{lead["id"]}/stage',
                 json={'stage': 'lost', 'lost_reason': 'price'})
    got = client.get(f'/api/storm/{_storm((*FOCO, 1.75))}').get_json()
    assert [h['id'] for h in got['by_tier']['lost_estimate']] == [lead['id']]


def test_an_untouched_imported_row_is_cold_not_an_open_lead(client):
    """It is not an "open lead" in any sense a rep would recognise — nobody has
    ever spoken to it. It is a cold address that happens to be in the table."""
    signup(client)
    lead = _lead_at(client, '4 Cold Ct', FOCO)
    with appmod.get_db() as db:
        db.execute("UPDATE leads SET import_batch='batch1', last_activity_at='' "
                   "WHERE id=?", (lead['id'],))
    got = client.get(f'/api/storm/{_storm((*FOCO, 1.75))}').get_json()
    assert [h['id'] for h in got['by_tier']['cold']] == [lead['id']]


# ── What the answer is allowed to claim ──────────────────────────────────────

def test_the_source_travels_with_the_answer(client):
    """Radar over the roof and a spotter's phone call from down the road are
    different claims, and a customer must never be told the weaker one as
    though it were the stronger."""
    signup(client)
    _lead_at(client, '12 Elm St', FOCO)
    got = client.get(f'/api/storm/{_storm((*FOCO, 1.75), source="spc_reports")}').get_json()
    assert got['source'] == 'spc_reports'


def test_an_unknown_storm_is_a_404_not_an_empty_answer(client):
    """"Nobody was hit" and "that storm is not in the archive" must not look
    the same."""
    signup(client)
    assert client.get('/api/storm/mrms_mesh:1999-01-01').status_code == 404


def test_below_the_threshold_is_not_a_hit(client):
    signup(client)
    _lead_at(client, '12 Elm St', FOCO)
    eid = _storm((*FOCO, 1.25))
    got = client.get(f'/api/storm/{eid}?min_size=1.5').get_json()
    assert all(not v for v in got['by_tier'].values())


def test_a_rep_only_sees_their_own_customers(client):
    signup(client, 'luke')                          # manager
    signup(client, 'casey')
    login(client, 'luke')
    _lead_at(client, '12 Elm St', FOCO)
    login(client, 'casey')
    got = client.get(f'/api/storm/{_storm((*FOCO, 1.75))}').get_json()
    assert all(not v for v in got['by_tier'].values())


def test_stored_storms_can_be_listed(client):
    signup(client)
    _storm((*FOCO, 1.75), date='2026-06-12')
    _storm((*GREELEY, 2.0), date='2026-07-04')
    dates = [e['event_date'] for e in client.get('/api/storms').get_json()]
    assert dates == ['2026-07-04', '2026-06-12']
