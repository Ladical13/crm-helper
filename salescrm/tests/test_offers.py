"""Offers: the programs each client type is offered, sent as a link.

These are customer-facing promises, so what is pinned is the gate: an offer
cannot go live with an [AMOUNT] nobody set, a claim we cannot back up, or no
link to itself; a draft is never visible to a client; and a rep only sees
the offers that fit the lead in front of them.
"""
import json
import os

import pytest

import app as appmod
from conftest import signup, new_lead, logout

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _seed():
    with open(os.path.join(HERE, 'offers.json'), encoding='utf-8') as f:
        return json.load(f)['offers']


def _offer(client, key):
    return next(o for o in client.get('/api/offers').get_json() if o['key'] == key)


def test_every_live_starter_offer_passes_the_go_live_gate():
    for o in _seed():
        if o['status'] == 'live':
            assert appmod._offer_problems(o, going_live=True) == [], o['key']


def test_every_offer_links_to_itself():
    for o in _seed():
        assert '{offer_link}' in o['email_body'] and '{offer_link}' in o['text_body'], o['key']


def test_an_offer_with_an_unset_amount_cannot_go_live(client):
    signup(client)
    r = client.put('/api/offers/past_referral', json={'status': 'live'})
    assert r.status_code == 400 and '[AMOUNT]' in r.get_json()['error']
    body = _offer(client, 'past_referral')
    fixed = {k: (v.replace('[AMOUNT]', '$100') if isinstance(v, str) else v)
             for k, v in body.items() if k in ('intro', 'email_body', 'text_body')}
    fixed['bullets'] = [b.replace('[AMOUNT]', '$100') for b in body['bullets']]
    assert client.put('/api/offers/past_referral', json=dict(fixed, status='live')).status_code == 200


@pytest.mark.parametrize('claim', ['Guaranteed 24-hour response', "Colorado's best roofer",
                                   '500+ roofs installed', 'A+ BBB rating'])
def test_a_claim_we_cannot_back_up_blocks_going_live(client, claim):
    signup(client)
    r = client.put('/api/offers/realtor_partner', json={'headline': claim, 'status': 'live'})
    assert r.status_code == 400 and 'cannot back up' in r.get_json()['error']


def test_a_draft_is_never_shown_to_a_client(client):
    signup(client)                                        # admin can preview it
    assert client.get('/offer/insurance_agent_partner').status_code == 200
    signup(client, 'casey')                               # a rep cannot
    assert client.get('/offer/insurance_agent_partner').status_code == 404
    logout(client)                                        # nor can the public
    assert client.get('/offer/insurance_agent_partner').status_code == 404
    assert client.get('/offer/realtor_partner').status_code == 200


def test_the_page_carries_the_sending_reps_name(client):
    signup(client)
    logout(client)
    page = client.get('/offer/realtor_partner?r=luke').get_data(as_text=True)
    assert 'Luke' in page and 'luke@' in page
    anon = client.get('/offer/realtor_partner?r=nobody').get_data(as_text=True)
    assert 'Project One Roofing</b>' in anon


def test_a_rep_is_offered_only_what_fits_the_lead(client):
    signup(client)
    realtor = new_lead(client, lead_type='realtor', email='a@b.com')
    offers = client.get(f'/api/leads/{realtor["id"]}/messages').get_json()['offers']
    assert [o['key'] for o in offers] == ['realtor_partner']
    assert '/offer/realtor_partner?r=luke' in offers[0]['text']


def test_past_client_offers_reach_past_clients_once_live(client):
    signup(client)
    cust = new_lead(client, email='a@b.com')
    client.patch(f'/api/leads/{cust["id"]}/stage', json={'stage': 'won'})
    keys = lambda: [o['key'] for o in client.get(f'/api/leads/{cust["id"]}/messages').get_json()['offers']]
    assert 'past_storm_check' not in keys()                # still a draft
    client.put('/api/offers/past_storm_check', json={'status': 'live'})
    assert 'past_storm_check' in keys()


def test_sending_an_offer_is_recorded_and_counted(client):
    signup(client)
    lead = new_lead(client, lead_type='realtor', email='a@b.com')
    client.post(f'/api/leads/{lead["id"]}/outcome', json={'outcome': 'emailed', 'offer': 'realtor_partner'})
    assert _offer(client, 'realtor_partner')['sent'] == 1


def test_only_a_manager_edits_offers(client):
    signup(client)
    signup(client, 'casey')
    assert client.put('/api/offers/realtor_partner', json={'name': 'x'}).status_code == 403
    assert all(o['status'] == 'live' for o in client.get('/api/offers').get_json())


def test_an_untouched_seeded_offer_follows_the_starter_file(client):
    signup(client)
    with appmod.get_db() as db:
        db.execute("UPDATE offers SET headline='old wording' WHERE key='realtor_partner'")
    appmod.seed_offers()
    assert _offer(client, 'realtor_partner')['headline'] != 'old wording'


def test_a_managers_edit_is_never_overwritten_by_the_starter_file(client):
    signup(client)
    client.put('/api/offers/realtor_partner', json={'headline': 'Our own headline'})
    appmod.seed_offers()
    assert _offer(client, 'realtor_partner')['headline'] == 'Our own headline'


def test_no_offer_mentions_a_service_we_do_not_sell():
    for o in _seed():
        text = json.dumps(o).lower()
        assert 'doors' not in text, o['key']
