"""Smoke tests for the sales-CRM invariants that matter most:
stage transitions, cadence/task advancement, and per-rep visibility."""
from conftest import signup, new_lead


def test_first_user_is_admin(client):
    me = signup(client).get_json()
    assert me['is_admin'] is True
    assert me['role'] == 'admin'


def test_second_user_is_rep(client):
    signup(client, 'luke')
    client.post('/api/logout')
    me = signup(client, 'bryan').get_json()
    assert me['is_admin'] is False
    assert me['role'] == 'rep'


def test_bad_signup_code_rejected(client):
    r = client.post('/api/signup', json={'signup_code': 'wrong', 'username': 'x', 'password': 'secret1'})
    assert r.status_code == 403


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
    client.post('/api/logout')
    # a rep signs up and should NOT see the admin's lead
    signup(client, 'bryan')
    leads = client.get('/api/leads').get_json()
    assert all(l['id'] != admin_lead['id'] for l in leads)
    # and cannot open it directly
    assert client.get(f"/api/leads/{admin_lead['id']}").status_code == 404


def test_manager_sees_all(client):
    signup(client, 'luke')  # admin/manager
    client.post('/api/logout')
    signup(client, 'bryan')
    rep_lead = new_lead(client, first_name='Rep', last_name='Lead')
    client.post('/api/logout')
    # log back in as admin
    client.post('/api/login', json={'username': 'luke', 'password': 'secret1'})
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
    assert j['project']['status'] == 'lead'
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
    assert dry['project']['name'].startswith('Window Cleaning - ')


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
