"""Outreach email drafts.

These are drafts a rep opens in their own Gmail — never sent from here. The
things worth guarding are the ones that would embarrass a rep in front of a
partner: a visible empty slot, the wrong step, or a tired opener.
"""
import app as appmod
from conftest import signup, new_lead


def _import(client, rows, lead_type='hoa'):
    return client.post('/api/prospects/import',
                       json={'rows': rows, 'lead_type': lead_type,
                             'source': 'dora'}).get_json()


# ── Rendering ────────────────────────────────────────────────────────────────

def test_draft_fills_the_slots(client):
    signup(client, 'luke')
    lead = new_lead(client, first_name='Kerry', last_name='Grimes',
                    company='Windsor Highlands', city='Windsor',
                    lead_type='property_manager')
    d = client.get(f"/api/leads/{lead['id']}/draft").get_json()
    assert 'Kerry' in d['body']
    assert 'Windsor Highlands' in d['subject']
    assert '{' not in d['body'] and '{' not in d['subject']


def test_a_nameless_lead_gets_a_generic_greeting(client):
    """Most HOA records have a company and no person; 'Hi ,' would look broken."""
    signup(client)
    lead = new_lead(client, first_name='', last_name='',
                    company='Centerra Marketplace Association', lead_type='hoa')
    d = client.get(f"/api/leads/{lead['id']}/draft").get_json()
    assert d['body'].startswith('Hi there,')
    assert 'Hi ,' not in d['body']


def test_an_unresearched_lead_gets_a_shorter_email_not_a_gap(client):
    """An empty hook must drop its paragraph rather than leave a hole."""
    signup(client)
    bare = new_lead(client, first_name='A', company='A Co', lead_type='hoa')
    d = client.get(f"/api/leads/{bare['id']}/draft").get_json()
    assert '\n\n\n' not in d['body']
    assert '{hook}' not in d['body']

    client.put(f"/api/leads/{bare['id']}", json={'hook': 'Saw the hail on 7/12.'})
    withhook = client.get(f"/api/leads/{bare['id']}/draft").get_json()
    assert 'Saw the hail on 7/12.' in withhook['body']
    assert len(withhook['body']) > len(d['body'])


def test_city_falls_back_when_unknown(client):
    signup(client)
    lead = new_lead(client, first_name='A', company='A Co', city='', lead_type='realtor')
    d = client.get(f"/api/leads/{lead['id']}/draft").get_json()
    assert 'the Front Range' in d['body']


def test_the_signature_is_the_rep_not_the_owner(client):
    """Reps send these from their own mailbox, so they sign them."""
    signup(client, 'bryan')
    lead = new_lead(client, first_name='A', company='A Co', lead_type='hoa')
    d = client.get(f"/api/leads/{lead['id']}/draft").get_json()
    assert 'Bryan' in d['body']
    assert 'Project One Roofing' in d['body']


def test_each_partner_type_has_its_own_angle(client):
    signup(client)
    seen = {}
    for lt in ('hoa', 'property_manager', 'realtor', 'insurance_agent'):
        lead = new_lead(client, first_name='A', company='A Co', city='Loveland',
                        lead_type=lt)
        seen[lt] = client.get(f"/api/leads/{lead['id']}/draft").get_json()['subject']
    assert len(set(seen.values())) == 4, seen


def test_an_unmapped_lead_type_still_gets_a_draft(client):
    """Homeowners aren't the target here, but the card must not error."""
    signup(client)
    lead = new_lead(client, first_name='A', company='A Co', lead_type='homeowner')
    assert client.get(f"/api/leads/{lead['id']}/draft").status_code == 200


# ── Step selection ───────────────────────────────────────────────────────────

def test_step_advances_with_touches(client):
    assert appmod._draft_step(0) == 'first'
    assert appmod._draft_step(1) == 'followup'
    assert appmod._draft_step(2) == 'followup'
    assert appmod._draft_step(3) == 'breakup'


def test_the_draft_moves_on_after_a_touch(client):
    signup(client)
    lead = new_lead(client, first_name='A', company='A Co', lead_type='hoa')
    assert client.get(f"/api/leads/{lead['id']}/draft").get_json()['step'] == 'first'
    client.post(f"/api/leads/{lead['id']}/activities", json={'kind': 'email'})
    assert client.get(f"/api/leads/{lead['id']}/draft").get_json()['step'] == 'followup'


def test_notes_are_not_touches(client):
    """Only real outreach advances the sequence."""
    signup(client)
    lead = new_lead(client, first_name='A', company='A Co', lead_type='hoa')
    client.post(f"/api/leads/{lead['id']}/activities", json={'kind': 'note'})
    assert client.get(f"/api/leads/{lead['id']}/draft").get_json()['step'] == 'first'


def test_step_can_be_forced(client):
    signup(client)
    lead = new_lead(client, first_name='A', company='A Co', lead_type='hoa')
    d = client.get(f"/api/leads/{lead['id']}/draft?step=breakup").get_json()
    assert d['step'] == 'breakup'


# ── Voice ────────────────────────────────────────────────────────────────────

def test_no_template_uses_a_banned_opener():
    """The phrases every partner has already deleted twice this week."""
    banned = appmod.TEMPLATES['banned_phrases']
    assert banned, 'banned_phrases went missing from outreach_templates.json'
    for lead_type, steps in appmod.TEMPLATES['templates'].items():
        for step, tpl in steps.items():
            text = (tpl['subject'] + ' ' + tpl['body']).lower()
            for phrase in banned:
                assert phrase not in text, f'{lead_type}/{step} uses "{phrase}"'


def test_every_template_stays_under_a_hundred_words():
    for lead_type, steps in appmod.TEMPLATES['templates'].items():
        for step, tpl in steps.items():
            words = len(tpl['body'].split())
            assert words <= 100, f'{lead_type}/{step} is {words} words'


def test_every_partner_type_has_all_three_steps():
    for lt in ('hoa', 'property_manager', 'realtor', 'insurance_agent',
               'adjuster', 'referral_partner'):
        steps = appmod.TEMPLATES['templates'][lt]
        assert set(steps) == {'first', 'followup', 'breakup'}, lt


def test_templates_only_use_slots_the_renderer_fills():
    import re
    # research_hook / storm_hook are Nimbus enrichment slots; they render
    # blank (and drop their paragraph) for hand-entered leads that never
    # went through the AI agents. See _render_draft + _first_line.
    known = {'greeting', 'first_name', 'company', 'city', 'hook',
             'rep_name', 'rep_first', 'research_hook', 'storm_hook'}
    text = str(appmod.TEMPLATES['templates']) + appmod.TEMPLATES['signature']
    for slot in set(re.findall(r'\{(\w+)\}', text)):
        assert slot in known, f'template uses unknown slot {{{slot}}}'


# ── The queue carries them ───────────────────────────────────────────────────

def test_queue_cards_arrive_with_their_draft(client):
    signup(client)
    _import(client, [{'company': 'Ridge HOA', 'license_no': 'L-1', 'city': 'Loveland'}])
    card = client.get('/api/queue/today').get_json()['new'][0]
    assert card['draft']['step'] == 'first'
    assert 'Ridge HOA' in card['draft']['subject']


def test_queue_draft_reflects_the_hook(client):
    signup(client)
    _import(client, [{'company': 'Ridge HOA', 'license_no': 'L-1',
                      'hook': 'Your 1998 shakes are past their service life.'}])
    card = client.get('/api/queue/today').get_json()['new'][0]
    assert 'past their service life' in card['draft']['body']


def test_draft_respects_lead_visibility(client):
    signup(client, 'luke')
    lead = new_lead(client, first_name='A', company='A Co')
    signup(client, 'bryan')
    assert client.get(f"/api/leads/{lead['id']}/draft").status_code == 404


def test_every_nimbus_segment_has_its_own_template():
    """`_render_draft` falls back to the referral_partner template for an
    unknown lead_type. That is worse than no draft: a church got copy about
    referring us business, and nothing anywhere said so."""
    import json
    import os
    from salescrm import app as sapp

    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    with open(os.path.join(here, 'outreach_templates.json'), encoding='utf-8') as f:
        tpls = json.load(f)['templates']

    # Everything Nimbus can put in a queue needs its own voice.
    for segment in ('church', 'school', 'school_district', 'commercial', 'gc'):
        assert segment in tpls, f'{segment} would fall back to referral_partner'
        for step in ('first', 'followup', 'breakup'):
            assert tpls[segment][step]['subject'].strip()
            assert tpls[segment][step]['body'].strip()


def test_open_data_segments_never_paste_their_hook_into_an_email():
    """For the partner types {hook} is a researched sentence. For the
    open-data segments it is a data string — "Office Building · 24,000 sq ft ·
    built 1994 · Owner mail: PO Box 580, Fort Collins" — and dropping that
    into a body reads as machine output AND quotes the recipient's own
    mailing address back at them."""
    import json
    import os

    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    with open(os.path.join(here, 'outreach_templates.json'), encoding='utf-8') as f:
        tpls = json.load(f)['templates']

    for segment in ('church', 'school', 'school_district', 'commercial', 'gc'):
        for step, tpl in tpls[segment].items():
            assert '{hook}' not in tpl['body'], \
                f'{segment}.{step} would paste a data string into an email'


def test_a_school_district_draft_renders_without_a_contact_name():
    """Open data gives an organisation, not a person. Most of these leads have
    no first name at all and must still read like a real email."""
    from salescrm import app as sapp

    draft = sapp._render_draft(
        {'lead_type': 'school_district', 'company': 'Poudre School District R-1',
         'city': 'Fort Collins', 'first_name': '', 'hook': '42 schools · 24,963 students'},
        'first', 'Luke Durnbaugh')
    assert draft is not None
    assert 'Hi there,' in draft['body']
    assert 'Poudre School District R-1' in draft['subject']
    assert '24,963 students' not in draft['body']
    assert 'Hi ,' not in draft['body']
