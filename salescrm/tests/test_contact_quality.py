"""Contact quality: handing reps the person who decides, not the front desk.

What is worth pinning: the grade (shared inboxes and switchboards are not a
contact), the queue working the best contacts first, "wrong contact" clearing
ONLY what research filled in, and a re-research using the rep's hint.
"""
import pytest

import app as appmod
from conftest import signup, new_lead


def _org(client, ref, **row):
    client.post('/api/prospects/import', json={
        'rows': [dict({'company': 'Grace Church', 'city': 'Loveland', 'source_ref': ref}, **row)],
        'lead_type': 'church', 'source': 'prospecting'})
    with appmod.get_db() as db:
        return db.execute('SELECT id FROM leads WHERE source_ref=?', (ref,)).fetchone()['id']


def _get(client, lid):
    return client.get(f'/api/leads/{lid}').get_json()


@pytest.mark.parametrize('lead,grade', [
    ({}, 0),
    ({'phone': '970-555-0100'}, 1),                                        # switchboard
    ({'first_name': 'John', 'email': 'info@grace.org'}, 1),               # shared inbox
    ({'first_name': 'John', 'email': 'john@grace.org'}, 2),
    ({'first_name': 'Dana', 'phone': '970', 'lead_type': 'homeowner'}, 2),  # her own phone
    ({'email': 'john@grace.org', 'contact_verified_at': 'x'}, 3),
])
def test_the_grade(lead, grade):
    assert appmod._contact_quality(lead) == grade


def test_the_queue_works_the_best_contacts_first(client):
    signup(client)
    weak = _org(client, 'q1', phone='970-555-0101', icp_score=9)
    strong = _org(client, 'q2', phone='970-555-0102', first_name='John',
                  email='john@grace.org', icp_score=1)
    ids = [n['id'] for n in client.get('/api/queue/today').get_json()['new']]
    assert ids.index(strong) < ids.index(weak)


def test_confirming_a_contact_makes_it_verified(client):
    signup(client)
    lid = _org(client, 'v1', phone='970-555-0101', first_name='John', email='john@grace.org')
    r = client.post(f'/api/leads/{lid}/contact/verify').get_json()
    assert (r['contact_quality'], r['contact_verified_by']) == (3, 'luke')


def test_wrong_contact_clears_only_what_research_filled(client):
    signup(client)
    researched = _org(client, 'w1', phone='970-555-0101')
    typed = _org(client, 'w2', phone='970-555-0102', first_name='Pat', email='pat@grace.org')
    with appmod.get_db() as db:
        db.execute("UPDATE leads SET first_name='John', email='john@grace.org', "
                   "contact_source='research' WHERE id=?", (researched,))
    client.post(f'/api/leads/{researched}/contact/wrong', json={'note': 'Mike runs facilities'})
    client.post(f'/api/leads/{typed}/contact/wrong', json={})
    a, b = _get(client, researched), _get(client, typed)
    assert (a['first_name'], a['email']) == ('', '')
    assert (b['first_name'], b['email']) == ('Pat', 'pat@grace.org')
    assert any('Mike runs facilities' in x['body'] for x in a['activities'])


def test_research_now_uses_the_hint_and_fills_empty_fields(client, monkeypatch):
    signup(client)
    lid = _org(client, 'r1', phone='970-555-0101')
    from agents.b2b import reenrich
    seen = {}

    def fake(lead, hint=''):
        seen['hint'] = hint
        return ({'decision_maker': {'name': 'Mike Ross', 'title': 'Facilities',
                                    'email': 'mross@grace.org'}},
                ['https://grace.org/staff'], 0.01)
    monkeypatch.setattr(reenrich, 'research', fake)
    r = client.post(f'/api/leads/{lid}/research', json={'hint': 'ask for facilities'}).get_json()
    assert seen['hint'] == 'ask for facilities'
    assert (r['first_name'], r['email'], r['contact_source']) == ('Mike', 'mross@grace.org', 'research')
    assert r['contact_quality'] == 2


def test_the_pipeline_filters_by_contact_quality(client):
    signup(client)
    _org(client, 'f1', phone='970-555-0101')
    good = _org(client, 'f2', phone='970-555-0102', first_name='John', email='john@grace.org')
    ids = [l['id'] for l in client.get('/api/leads?contact_quality=2').get_json()]
    assert ids == [good]


def test_an_edit_regrades_the_lead(client):
    signup(client)
    lid = _org(client, 'e1', phone='970-555-0101')
    client.put(f'/api/leads/{lid}', json={'first_name': 'John', 'email': 'john@grace.org'})
    assert _get(client, lid)['contact_quality'] == 2
