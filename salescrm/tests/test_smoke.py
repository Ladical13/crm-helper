"""Smoke tests for the sales-CRM invariants that matter most:
stage transitions, cadence/task advancement, and per-rep visibility."""
from conftest import signup, login, logout, new_lead
import app as appmod


def test_first_user_is_admin(client):
    me = signup(client).get_json()
    assert me['is_admin'] is True
    assert me['role'] == 'admin'


def test_second_user_is_rep(client):
    signup(client, 'luke')
    logout(client)
    me = signup(client, 'bryan').get_json()
    assert me['is_admin'] is False
    assert me['role'] == 'rep'


def test_this_app_does_not_do_auth(client):
    """Login, signup and invites moved to the portal. Enforcing the signup code
    is covered by portal/tests/test_auth.py; what matters here is that this app
    no longer offers a second way in."""
    paths = {str(r) for r in client.application.url_map.iter_rules()}
    assert not paths & {'/api/login', '/api/logout', '/api/signup', '/api/invites'}


def test_stage_change_logs_activity_and_won_closes_tasks(client):
    signup(client)
    lead = new_lead(client)
    lid = lead['id']
    client.post(f'/api/leads/{lid}/tasks', json={'title': 'call', 'kind': 'call'})
    # move to won
    r = client.patch(f'/api/leads/{lid}/stage', json={'stage': 'won'})
    assert r.get_json()['stage'] == 'won'
    full = client.get(f'/api/leads/{lid}').get_json()
    # a stage_change activity was recorded
    assert any(a['kind'] == 'stage_change' for a in full['activities'])
    # won closed all open tasks
    assert all(t['done'] for t in full['tasks'])
    # won_at stamped
    assert full['won_at']


def test_cadence_enroll_and_advance(client):
    signup(client)
    lead = new_lead(client)
    lid = lead['id']
    r = client.post(f'/api/leads/{lid}/enroll', json={'cadence_id': 'new_lead_7touch'})
    assert r.status_code == 201
    # exactly one task materialized at enroll
    tasks = client.get('/api/tasks?scope=all').get_json()
    assert len(tasks) == 1
    first = tasks[0]
    # completing it advances the cadence -> next step task appears
    client.patch(f'/api/tasks/{first["id"]}', json={'done': True})
    open_tasks = [t for t in client.get('/api/tasks?scope=all').get_json() if not t['done']]
    assert len(open_tasks) == 1
    assert open_tasks[0]['id'] != first['id']


def test_double_enroll_rejected(client):
    signup(client)
    lid = new_lead(client)['id']
    client.post(f'/api/leads/{lid}/enroll', json={'cadence_id': 'new_lead_7touch'})
    r = client.post(f'/api/leads/{lid}/enroll', json={'cadence_id': 'new_lead_7touch'})
    assert r.status_code == 409


def test_next_action_tracks_soonest_task(client):
    signup(client)
    lid = new_lead(client)['id']
    client.post(f'/api/leads/{lid}/tasks', json={'title': 'later', 'due_at': '2030-01-01T00:00:00Z'})
    client.post(f'/api/leads/{lid}/tasks', json={'title': 'sooner', 'due_at': '2026-01-01T00:00:00Z'})
    lead = client.get(f'/api/leads/{lid}').get_json()
    assert lead['next_action_at'] == '2026-01-01T00:00:00Z'


def test_rep_visibility_isolation(client):
    # admin creates a lead owned by admin
    signup(client, 'luke')
    admin_lead = new_lead(client, first_name='Admin', last_name='Owned')
    logout(client)
    # a rep signs up and should NOT see the admin's lead
    signup(client, 'bryan')
    leads = client.get('/api/leads').get_json()
    assert all(l['id'] != admin_lead['id'] for l in leads)
    # and cannot open it directly
    assert client.get(f"/api/leads/{admin_lead['id']}").status_code == 404


def test_manager_sees_all(client):
    signup(client, 'luke')  # admin/manager
    logout(client)
    signup(client, 'bryan')
    rep_lead = new_lead(client, first_name='Rep', last_name='Lead')
    logout(client)
    # log back in as admin
    login(client, 'luke')
    leads = client.get('/api/leads').get_json()
    assert any(l['id'] == rep_lead['id'] for l in leads)


def test_den_dry_run_builds_payload_without_writing(client):
    signup(client)
    lid = new_lead(client, first_name='Jane', last_name='Doe', phone='555', state='CO')['id']
    r = client.post(f'/api/leads/{lid}/convert?dry_run=1')
    j = r.get_json()
    assert j['dry_run'] is True
    assert j['contact']['name'] == 'Jane Doe'
    assert j['contact']['assigned_to'] == 'luke@projectoneroofing.com'
    # 'lead' was never a Base44 status. An un-won lead enters at 'new_lead'.
    assert j['project']['status'] == 'new_lead'
    # Without location_id the record is invisible to every executive query.
    assert j['contact']['location_id'] == appmod.CO_LOCATION_ID
    assert j['project']['location_id'] == appmod.CO_LOCATION_ID
    # nothing was persisted to the Den
    assert client.get(f'/api/leads/{lid}').get_json()['crm_contact_id'] == ''


def test_partners_referral_counts(client):
    signup(client)
    realtor = new_lead(client, lead_type='realtor', first_name='Sara', last_name='Kim')
    new_lead(client, first_name='A', referred_by=realtor['id'])
    won = new_lead(client, first_name='B', referred_by=realtor['id'])
    client.patch(f"/api/leads/{won['id']}/stage", json={'stage': 'won'})
    partners = client.get('/api/partners').get_json()
    sara = next(p for p in partners if p['id'] == realtor['id'])
    assert sara['referrals_total'] == 2
    assert sara['referrals_won'] == 1
    # Partner detail carries the referred projects inline
    detail = client.get(f"/api/leads/{realtor['id']}").get_json()
    assert len(detail['referrals']) == 2
    assert any(r['stage'] == 'won' for r in detail['referrals'])
    # Non-partner detail has no referrals key
    assert 'referrals' not in client.get(f"/api/leads/{won['id']}").get_json()


def test_dashboard_math(client):
    signup(client)
    a = new_lead(client, est_value=10000)
    b = new_lead(client, est_value=20000)
    client.patch(f"/api/leads/{a['id']}/stage", json={'stage': 'won'})
    client.patch(f"/api/leads/{b['id']}/stage", json={'stage': 'lost'})
    d = client.get('/api/dashboard').get_json()
    assert d['won_count'] == 1
    assert d['won_value'] == 10000
    assert d['win_rate'] == 50.0  # 1 won of 2 decided


def test_invalid_stage_and_type_rejected(client):
    signup(client)
    lid = new_lead(client)['id']
    assert client.patch(f'/api/leads/{lid}/stage', json={'stage': 'bogus'}).status_code == 400
    assert client.post('/api/leads', json={'lead_type': 'alien'}).status_code == 400
    assert client.post('/api/leads', json={'first_name': 'X', 'service': 'lawn_care'}).status_code == 400


def test_service_lines(client):
    signup(client)
    roof = new_lead(client, first_name='Roof', est_value=15000)          # defaults to roofing
    wc = new_lead(client, first_name='Windows', service='window_cleaning', est_value=400)
    assert roof['service'] == 'roofing' and roof['service_icon'] == '🏠'
    assert wc['service'] == 'window_cleaning' and wc['service_label'] == 'Window Cleaning'
    # filter
    only_wc = client.get('/api/leads?service=window_cleaning').get_json()
    assert [l['id'] for l in only_wc] == [wc['id']]
    # dashboard split
    client.patch(f"/api/leads/{wc['id']}/stage", json={'stage': 'won'})
    d = client.get('/api/dashboard').get_json()
    assert d['by_service']['roofing']['open'] == 1
    assert d['by_service']['window_cleaning']['won'] == 1
    assert d['by_service']['window_cleaning']['won_value'] == 400
    # Den project payload carries the service name
    dry = client.post(f"/api/leads/{wc['id']}/convert?dry_run=1").get_json()
    assert dry['project']['job_name'].startswith('Window Cleaning - ')


def test_plans_and_mrr(client):
    signup(client)
    # a monthly homeowner plan and a quarterly HOA plan
    hs = new_lead(client, first_name='Home', service='exterior_maintenance',
                  plan='home_shield', billing='monthly', est_value=99)
    hoa = new_lead(client, first_name='Oak', lead_type='hoa', service='exterior_maintenance',
                   plan='hoa_complete', billing='quarterly', est_value=900)
    assert hs['plan_name'] == 'Home Shield' and hs['value_suffix'] == '/mo'
    client.patch(f"/api/leads/{hs['id']}/stage", json={'stage': 'won'})
    client.patch(f"/api/leads/{hoa['id']}/stage", json={'stage': 'won'})
    d = client.get('/api/dashboard').get_json()
    assert d['active_plans'] == 2
    assert d['mrr'] == 399         # 99/mo + 900/qtr(=300)
    assert d['arr'] == 4788
    assert d['plan_mix']['Home Shield'] == 1
    # invalid billing rejected
    assert client.post('/api/leads', json={'first_name': 'x', 'billing': 'weekly'}).status_code == 400
    # plans catalog served
    assert {p['id'] for p in client.get('/api/plans').get_json()} >= {'home_shield', 'hoa_complete'}


def test_documents_crud(client):
    import io
    signup(client)
    lid = new_lead(client)['id']
    up = client.post(f'/api/leads/{lid}/documents',
                     data={'file': (io.BytesIO(b'%PDF-1.4 hi'), 'contract.pdf')},
                     content_type='multipart/form-data')
    assert up.status_code == 201
    docs = client.get(f'/api/leads/{lid}/documents').get_json()
    assert len(docs) == 1 and docs[0]['orig_name'] == 'contract.pdf'
    assert 'filename' not in docs[0]                       # on-disk name never exposed
    did = docs[0]['id']
    dl = client.get(f'/api/documents/{did}/download')
    assert dl.status_code == 200 and dl.data.startswith(b'%PDF')
    # disallowed extension rejected
    bad = client.post(f'/api/leads/{lid}/documents',
                      data={'file': (io.BytesIO(b'x'), 'evil.exe')},
                      content_type='multipart/form-data')
    assert bad.status_code == 400
    assert client.delete(f'/api/documents/{did}').status_code == 200
    assert client.get(f'/api/leads/{lid}/documents').get_json() == []


def test_document_visibility_isolation(client):
    import io
    signup(client, 'luke')
    lid = new_lead(client)['id']
    did = client.post(f'/api/leads/{lid}/documents',
                      data={'file': (io.BytesIO(b'x'), 'a.pdf')},
                      content_type='multipart/form-data').get_json()['id']
    logout(client)
    signup(client, 'bryan')                                # a different rep
    assert client.get(f'/api/documents/{did}/download').status_code == 403
    assert client.delete(f'/api/documents/{did}').status_code == 403


def test_search_reaches_past_the_limit_window(app, client):
    """?q= must search the whole table, not just the page ?limit= returned.

    The filter used to run in Python after the LIMIT, so a lead outside the most
    recently updated `limit` rows was unfindable. Harmless with a few hundred
    homeowners; with tens of thousands of imported prospects it hid almost
    everything.
    """
    signup(client)
    needle = new_lead(client, first_name='Zophia', last_name='Quintrell')['id']
    for i in range(5):
        new_lead(client, first_name=f'Filler{i}', last_name='Person')
    # Backdate it out of the window: the list sorts on updated_at, and leads
    # created in the same second would otherwise tie.
    with app.get_db() as db:
        db.execute("UPDATE leads SET updated_at='2020-01-01T00:00:00Z' WHERE id=?",
                   (needle,))

    assert needle not in [l['id'] for l in
                          client.get('/api/leads?limit=3').get_json()]
    hits = client.get('/api/leads?q=quintrell&limit=3').get_json()
    assert [l['id'] for l in hits] == [needle]

    # Matches on the other searchable columns, and stays case-insensitive.
    assert client.get('/api/leads?q=ZOPHIA').get_json()[0]['id'] == needle
    assert client.get('/api/leads?q=nobodyhere').get_json() == []


def test_search_treats_wildcards_literally(client):
    """A user typing % must not match every lead in the pipeline."""
    signup(client)
    new_lead(client, first_name='Alpha', last_name='One')
    assert client.get('/api/leads?q=%25').get_json() == []      # %25 -> '%'
    assert client.get('/api/leads?q=_').get_json() == []
    # and a literal underscore in the data is still findable
    lid = new_lead(client, company='Snake_Case HOA', first_name='', last_name='')['id']
    assert client.get('/api/leads?q=snake_case').get_json()[0]['id'] == lid


def test_service_migration_adds_column(app, client):
    # simulate a pre-service DB: drop the column, rerun migrate
    with app.get_db() as db:
        db.execute('ALTER TABLE leads DROP COLUMN service')
    app.migrate_db()
    with app.get_db() as db:
        cols = [r['name'] for r in db.execute('PRAGMA table_info(leads)')]
    assert 'service' in cols
    signup(client)
    assert new_lead(client)['service'] == 'roofing'
