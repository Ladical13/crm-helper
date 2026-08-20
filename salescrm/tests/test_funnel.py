"""The estimator→CRM funnel join, and the stage moves it drives.

Every one of these covers a seam that had no test at all, which is how the Won
handoff managed to be broken for its entire life: `_push_to_den` was guarded on
`crm_contact_id`, "Start estimate" set that field, and so the push was skipped
for every lead that ever reached an estimate. Nothing failed, nothing logged,
and no job ever arrived in The Den.

The rule these pin down is worth stating once: **stages move forward on real
events, and a rep is always allowed to be ahead of the automation.**
"""
import app as appmod
import pytest
from conftest import signup, new_lead

from portal import funnel as pfunnel


def _estimate(lead_id, state, est_id='est-1', value=0, contact_id='', token=''):
    """Stand in for the estimator recording a state change."""
    return pfunnel.record(est_id, state, lead_id=lead_id, contact_id=contact_id,
                          value=value, share_token=token)


def _stage(client, lead_id):
    return client.get(f'/api/leads/{lead_id}').get_json()['stage']


# ── The join itself ─────────────────────────────────────────────────────────

def test_starting_an_estimate_carries_the_lead_id(client):
    """Without the lead id on the handoff there is no funnel — the estimator
    can say an estimate was signed but not which door it came from."""
    signup(client)
    lid = new_lead(client)['id']
    r = client.post(f'/api/leads/{lid}/start-estimate').get_json()
    assert f'lead={lid}' in r['estimator_url']


def test_starting_an_estimate_writes_nothing_to_the_den(client):
    """It used to create a Base44 contact here, which both polluted production
    with leads that never closed and set the field the Won handoff read as
    'already pushed'."""
    signup(client)
    lid = new_lead(client)['id']
    client.post(f'/api/leads/{lid}/start-estimate')
    lead = client.get(f'/api/leads/{lid}').get_json()
    assert lead['crm_contact_id'] == ''
    assert lead['crm_project_id'] == ''


# ── Stage moves on real events ──────────────────────────────────────────────

def test_a_sent_estimate_advances_the_lead(client):
    signup(client)
    lid = new_lead(client)['id']
    _estimate(lid, 'sent')
    assert _stage(client, lid) == 'estimate_presented'


def test_a_signature_wins_the_lead(client):
    signup(client)
    lid = new_lead(client)['id']
    _estimate(lid, 'signed', value=18400)
    assert _stage(client, lid) == 'won'
    lead = client.get(f'/api/leads/{lid}').get_json()
    assert lead['est_value'] == 18400      # the signed number, not the guess
    assert lead['won_at']


def test_a_sent_estimate_starts_the_follow_up_cadence(client):
    """Silence is not a no. The estimate-follow-up cadence existed and never
    ran, because enrolling was a button nobody had a reason to press."""
    signup(client)
    lid = new_lead(client)['id']
    _estimate(lid, 'sent')
    client.get('/api/leads')               # drains the event
    r = client.post(f'/api/leads/{lid}/enroll', json={'cadence_id': 'estimate_followup'})
    assert r.status_code == 409            # already running


def test_a_lost_estimate_does_not_kill_the_lead(client):
    """Plenty get re-quoted; auto-losing would close the tasks that win it back."""
    signup(client)
    lid = new_lead(client)['id']
    _estimate(lid, 'lost')
    assert _stage(client, lid) != 'lost'
    bodies = [a['body'] for a in client.get(f'/api/leads/{lid}').get_json()['activities']]
    assert any('marked lost' in b.lower() for b in bodies)


def test_the_old_declined_spelling_still_works(client):
    """`declined` was renamed to `lost`, and real estimates still carry the old
    value. It has to behave identically rather than being silently ignored."""
    signup(client)
    lid = new_lead(client)['id']
    _estimate(lid, 'declined')
    assert _stage(client, lid) != 'lost'
    bodies = [a['body'] for a in client.get(f'/api/leads/{lid}').get_json()['activities']]
    assert any('marked lost' in b.lower() for b in bodies)


# ── The manual override ─────────────────────────────────────────────────────

def test_the_automation_never_walks_a_lead_backwards(client):
    """A rep who has moved further than the estimate suggests keeps their stage."""
    signup(client)
    lid = new_lead(client)['id']
    client.patch(f'/api/leads/{lid}/stage', json={'stage': 'follow_up'})
    _estimate(lid, 'sent')                 # implies the earlier estimate_presented
    assert _stage(client, lid) == 'follow_up'


def test_a_replayed_event_changes_nothing(client):
    signup(client)
    lid = new_lead(client)['id']
    _estimate(lid, 'signed')
    assert _stage(client, lid) == 'won'
    _estimate(lid, 'sent')                 # out-of-order delivery
    assert _stage(client, lid) == 'won'


def test_a_signature_overrides_a_lead_marked_lost(client):
    """The one case where the event outranks the rep: they signed."""
    signup(client)
    lid = new_lead(client)['id']
    client.patch(f'/api/leads/{lid}/stage', json={'stage': 'lost'})
    _estimate(lid, 'signed')
    assert _stage(client, lid) == 'won'


def test_an_event_is_applied_only_once(client):
    signup(client)
    lid = new_lead(client)['id']
    _estimate(lid, 'sent')
    client.get('/api/leads')
    client.patch(f'/api/leads/{lid}/stage', json={'stage': 'inspected'})
    client.get('/api/leads')               # the drained event must not re-fire
    assert _stage(client, lid) == 'inspected'


# ── Matching an estimate to a lead ──────────────────────────────────────────

def test_an_estimate_matches_by_den_contact_when_it_has_no_lead_id(client):
    """A rep who started in the estimator and picked the Den job still gets
    their lead moved — the contact id is the fallback join."""
    signup(client)
    lid = new_lead(client)['id']
    with appmod.get_db() as db:
        db.execute("UPDATE leads SET crm_contact_id='c-99' WHERE id=?", (lid,))
    _estimate('', 'signed', est_id='est-2', contact_id='c-99')
    assert _stage(client, lid) == 'won'


def test_an_orphan_estimate_is_dropped_quietly(client):
    """An estimate belonging to no lead must not crash the board."""
    signup(client)
    _estimate('', 'signed', est_id='est-3', contact_id='nobody')
    assert client.get('/api/leads').status_code == 200


# ── The Den handoff ─────────────────────────────────────────────────────────

def test_the_den_guard_reads_the_project_not_the_contact(client):
    """The bug, pinned. A lead that has a Den contact but no job is NOT pushed
    yet, and Won must still hand it over."""
    signup(client)
    lid = new_lead(client)['id']
    with appmod.get_db() as db:
        db.execute("UPDATE leads SET crm_contact_id='c-1' WHERE id=?", (lid,))

    calls = []
    original = appmod._push_to_den
    appmod._push_to_den = lambda lead_id, estimate=None: calls.append(lead_id) or {'ok': True}
    try:
        client.patch(f'/api/leads/{lid}/stage', json={'stage': 'won'})
    finally:
        appmod._push_to_den = original
    assert calls == [lid]


def test_a_lead_already_in_the_den_is_not_pushed_twice(client):
    signup(client)
    lid = new_lead(client)['id']
    with appmod.get_db() as db:
        db.execute("UPDATE leads SET crm_project_id='p-1' WHERE id=?", (lid,))

    calls = []
    original = appmod._push_to_den
    appmod._push_to_den = lambda lead_id, estimate=None: calls.append(lead_id) or {'ok': True}
    try:
        client.patch(f'/api/leads/{lid}/stage', json={'stage': 'won'})
    finally:
        appmod._push_to_den = original
    assert calls == []


def test_the_den_payload_carries_the_colorado_location(client):
    """Without it the job misses the filter every exec-team skill applies, and
    the estimator's own contact search, so it exists and is invisible."""
    signup(client)
    lid = new_lead(client, first_name='Jane', last_name='Doe')['id']
    j = client.post(f'/api/leads/{lid}/convert?dry_run=1').get_json()
    assert j['contact']['location_id'] == appmod.CO_LOCATION_ID
    assert j['project']['location_id'] == appmod.CO_LOCATION_ID
    assert j['project']['status'] == 'contracted'
