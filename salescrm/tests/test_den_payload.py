"""The salescrm -> Base44 ("The Den") handoff contract.

This is the seam where a sold job leaves the app that knows where it came from
and enters the app the executive team reports off. Anything not carried across
here is gone for good: Base44 cannot re-derive a lead's origin.

The whole payload was previously unguarded, which is how it drifted into
writing records that no executive query could see. Every mapping below is
pinned deliberately — if Base44's real schema differs, fix the constant in
app.py and these tests tell you exactly what moved.
"""
import app as appmod
from conftest import signup, new_lead


def _payload(client, lead_id):
    j = client.post(f'/api/leads/{lead_id}/convert?dry_run=1').get_json()
    return j['contact'], j['project']


# ── The invisibility bug ─────────────────────────────────────────────────────

def test_both_payloads_carry_location_id(client):
    """Without location_id a pushed record is invisible to the entire exec team.

    Every executive query filters `?q={"location_id": ...}`, and so does the
    estimator's own contact search. A record missing it is not "hard to find" —
    it does not exist as far as the CEO dashboard, pipeline health, job margin
    and referral intel are concerned.
    """
    signup(client)
    lid = new_lead(client)['id']
    contact, project = _payload(client, lid)
    assert contact['location_id'] == appmod.CO_LOCATION_ID
    assert project['location_id'] == appmod.CO_LOCATION_ID


def test_location_id_matches_the_estimators_constant():
    """The estimator filters its contact cache on the same id
    (`estimator/app.py` CO_LOCATION_ID). If these two ever disagree, a contact
    pushed from the CRM stops appearing in the estimator's search and
    `start-estimate` silently hands the rep an empty lookup. Read the constant
    textually rather than importing the estimator, which is a whole second app."""
    import os, re
    est = os.path.join(os.path.dirname(__file__), '..', '..', 'estimator', 'app.py')
    m = re.search(r'^CO_LOCATION_ID\s*=\s*["\']([^"\']+)["\']',
                  open(est).read(), re.M)
    assert m, 'estimator/app.py no longer defines CO_LOCATION_ID'
    assert m.group(1) == appmod.CO_LOCATION_ID


# ── Status vocabulary ────────────────────────────────────────────────────────

def test_a_won_lead_arrives_as_contracted(client):
    """A sold job is under contract, not a fresh lead. It previously arrived as
    status 'lead' — a value absent from Base44's vocabulary entirely — so signed
    work sat at the bottom of the production pipeline."""
    signup(client)
    lid = new_lead(client)['id']
    client.patch(f'/api/leads/{lid}/stage', json={'stage': 'won'})
    _, project = _payload(client, lid)
    assert project['status'] == 'contracted'


def test_status_does_not_depend_on_the_stage_the_card_sits_in(client):
    """The push fires at signature, so `contracted` is unconditionally true —
    there is no per-stage status map to keep in sync. Pinned because the old
    code sent `lead`, a value absent from The Den's vocabulary entirely."""
    signup(client)
    for stage in ('contacted', 'appt_set', 'inspected', 'estimate_presented'):
        lid = new_lead(client)['id']
        client.patch(f'/api/leads/{lid}/stage', json={'stage': stage})
        _, project = _payload(client, lid)
        assert project['status'] == 'contracted', stage


# ── Attribution: the fields that make partner ROI answerable ─────────────────

def test_referred_by_crosses_as_a_readable_name(client):
    """`referred_by` holds the partner's LEAD ID. Pushing the raw uuid would be
    worse than useless — referral-intel needs a name to tier a partner."""
    signup(client)
    partner = new_lead(client, lead_type='realtor',
                       first_name='Dana', last_name='Whitfield')
    lid = new_lead(client, first_name='Bob', referred_by=partner['id'])['id']
    _, project = _payload(client, lid)
    assert 'Dana Whitfield' in project['notes']
    assert partner['id'] not in project['notes']


def test_a_company_partner_falls_back_to_its_company_name(client):
    signup(client)
    partner = new_lead(client, lead_type='hoa', first_name='', last_name='',
                       company='Ridge HOA')
    lid = new_lead(client, referred_by=partner['id'])['id']
    _, project = _payload(client, lid)
    assert 'Ridge HOA' in project['notes']


def test_deal_value_crosses_as_contract_value(client):
    """est_value was dropped entirely, so a won job reached the exec team with
    no number attached to it."""
    signup(client)
    lid = new_lead(client, est_value=18500)['id']
    _, project = _payload(client, lid)
    assert project['contract_value'] == 18500.0


def test_campaign_and_provenance_survive_the_handoff(client):
    """An imported lead's batch and source_ref are the only link between agent
    spend and revenue. Losing them collapses every channel into one bucket."""
    signup(client)
    client.post('/api/prospects/import', json={
        'rows': [{'company': 'Greystar FC', 'phone': '9705551212',
                  'source_ref': 'nimbus:pm:fc-0001'}],
        'lead_type': 'property_manager', 'source': 'nimbus',
        'batch': 'nimbus-2026-08', 'dry_run': False})
    lid = client.get('/api/leads').get_json()[0]['id']
    _, project = _payload(client, lid)
    assert 'nimbus-2026-08' in project['notes']
    assert 'nimbus:pm:fc-0001' in project['notes']
    assert 'Property Manager' in project['notes']


def test_a_plain_homeowner_gets_no_lead_type_noise(client):
    """Provenance is signal, not boilerplate — don't label every homeowner."""
    signup(client)
    lid = new_lead(client, lead_type='homeowner')['id']
    _, project = _payload(client, lid)
    assert 'Lead type' not in project['notes']


# ── Source channel ───────────────────────────────────────────────────────────

def test_the_channel_passes_through_intact(client):
    """Base44's `source` is free text, so the channel crosses unnormalised.
    Flattening it into a generic bucket is what destroyed attribution in the
    first place — an unrecognised campaign name is better preserved than
    mapped away into something lossy."""
    signup(client)
    lid = new_lead(client, source='storm')['id']
    contact, _ = _payload(client, lid)
    assert contact['source'] == 'storm'


# ── Documented required fields ───────────────────────────────────────────────

def test_project_carries_every_documented_required_field(client):
    """Per the Base44 field map, these are required on the Project entity."""
    signup(client)
    lid = new_lead(client, phone='9705551212', address='123 Oak St',
                   city='Fort Collins', state='CO')['id']
    _, project = _payload(client, lid)
    for field in ('name', 'client_name', 'client_phone', 'street_address',
                  'city', 'state', 'assigned_salesperson', 'status'):
        assert project.get(field) not in (None, ''), field


def test_rep_travels_on_the_documented_project_field(client):
    """Base44's Project uses `assigned_salesperson`; `assigned_to` is the
    Contact's field. Sending the wrong one leaves every job unassigned."""
    signup(client)
    lid = new_lead(client)['id']
    contact, project = _payload(client, lid)
    assert contact['assigned_to'] == 'luke@projectoneroofing.com'
    assert project['assigned_salesperson'] == 'luke@projectoneroofing.com'


def test_a_push_never_writes_during_a_dry_run(client):
    signup(client)
    lid = new_lead(client)['id']
    _payload(client, lid)
    assert client.get(f'/api/leads/{lid}').get_json()['crm_contact_id'] == ''


def test_a_hand_typed_partner_name_is_not_discarded(client):
    """`referred_by` normally holds a lead id, but the API accepts free text.
    A name typed by a rep must survive rather than being dropped as an
    unresolvable id."""
    signup(client)
    lid = new_lead(client, referred_by='Dana Whitfield (State Farm)')['id']
    _, project = _payload(client, lid)
    assert 'Referred by: Dana Whitfield (State Farm)' in project['notes']


def test_an_unresolvable_id_is_not_leaked_as_a_name(client):
    """A uuid that matches no lead is a dangling pointer, not a partner. Better
    to omit it than to print a uuid at the exec team."""
    signup(client)
    lid = new_lead(client, referred_by='11111111-2222-3333-4444-555555555555')['id']
    _, project = _payload(client, lid)
    assert 'Referred by' not in project['notes']
