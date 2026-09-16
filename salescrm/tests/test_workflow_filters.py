"""Work queues must find actionable records before pagination and respect ownership."""
from conftest import signup, login, new_lead
from test_queue import _import, _set, _ago


def test_contact_filters_apply_before_limit_and_preserve_rep_boundary(client):
    signup(client, 'luke')
    _import(client, [{'company': f'Research {i}', 'source_ref': f'r:{i}'} for i in range(12)])
    _import(client, [{'company': 'Ready', 'source_ref': 'ready', 'email': 'ready@example.com'}])
    ready = client.get('/api/leads?contact=ready&limit=1').get_json()
    assert len(ready) == 1 and ready[0]['company'] == 'Ready'
    research = client.get('/api/leads?contact=research&limit=5').get_json()
    assert len(research) == 5 and all(not r['email'] and not r['phone'] for r in research)
    signup(client, 'bryan')
    assert client.get('/api/leads?contact=ready&rep=luke').get_json() == []


def test_next_step_priority_excludes_untouched_closed_and_scheduled(client, app):
    signup(client)
    _import(client, [{'company': name, 'source_ref': name} for name in ['Untouched', 'Active', 'Closed', 'Scheduled']])
    rows = {r['company']: r for r in client.get('/api/leads').get_json()}
    for name in ['Active', 'Closed', 'Scheduled']:
        _set(rows[name]['id'], last_activity_at=_ago(2))
    _set(rows['Closed']['id'], stage='lost')
    client.post('/api/leads/'+rows['Scheduled']['id']+'/tasks', json={'title':'Call back','due_at':_ago(-1)})
    result = client.get('/api/leads?attention=needs_step').get_json()
    assert [r['company'] for r in result] == ['Active']


def test_ready_queue_and_research_do_not_hide_due_visits(client):
    signup(client)
    _import(client, [{'company': 'Research', 'source_ref': 'research', 'icp_score': 10},
                     {'company': 'Ready', 'source_ref': 'ready', 'phone': '9705550123', 'icp_score': 1}])
    lead = new_lead(client, first_name='Visit', phone='', email='')
    q = client.get('/api/queue/today?contact=ready&target=3').get_json()
    assert any(d['lead_id'] == lead['id'] for d in q['due'])
    assert [r['company'] for r in q['new']] == ['Ready']
    research = client.get('/api/queue/today?contact=research').get_json()
    assert research['due'] == []
    assert [r['company'] for r in research['new']] == ['Research']
    assert research['done_today'] == 0


def test_live_domain_suppression_replaces_due_card_and_fills_queue(client):
    signup(client)
    hidden = new_lead(client, email='person@blocked.example')
    visible = new_lead(client, email='person@allowed.example')
    client.post('/api/suppressions', json={'kind':'domain','value':'blocked.example'})
    q = client.get('/api/queue/today?target=1&contact=ready').get_json()
    assert [d['lead_id'] for d in q['due']] == [visible['id']]
    assert all(d['lead_id'] != hidden['id'] for d in q['due'])


def test_summary_can_be_personal_for_managers_but_never_cross_rep(client):
    signup(client, 'luke')
    new_lead(client, est_value=500)
    signup(client, 'bryan')
    new_lead(client, est_value=100)
    assert client.get('/api/pipeline/summary?rep=luke').get_json()['open_value'] == 100
    login(client, 'luke')
    assert client.get('/api/pipeline/summary?rep=luke').get_json()['open_value'] == 500
    assert client.get('/api/pipeline/summary').get_json()['open_value'] == 600


def test_list_pagination_is_stable_and_city_is_searchable(client):
    signup(client)
    _import(client, [{'company':f'Company {i}', 'source_ref':f'city:{i}', 'city':'Loveland'} for i in range(6)])
    first = client.get('/api/leads?q=Loveland&limit=3').get_json()
    second = client.get('/api/leads?q=Loveland&limit=3&offset=3').get_json()
    assert len(first) == len(second) == 3
    assert len({r['id'] for r in first+second}) == 6
