"""The daily outreach queue.

The queue is the whole product for a rep, so the rules that keep it trustworthy
are the ones worth pinning: an opted-out partner never reappears, a partner
touched this week is left alone, and the day's target counts work already done
rather than demanding fresh names on top of it.
"""
from datetime import timedelta

import app as appmod
from conftest import signup, login, new_lead


def _import(client, rows, **kw):
    body = {'rows': rows, 'lead_type': 'hoa', 'source': 'dora'}
    body.update(kw)
    return client.post('/api/prospects/import', json=body).get_json()


def _prospects(n, score=0):
    return [{'company': f'HOA {i}', 'license_no': f'L-{i}', 'city': 'Fort Collins',
             'icp_score': score} for i in range(n)]


def _set(lead_id, **cols):
    sets = ', '.join(f'{k}=?' for k in cols)
    with appmod.get_db() as db:
        db.execute(f'UPDATE leads SET {sets} WHERE id=?', list(cols.values()) + [lead_id])


def _ago(days):
    return appmod._iso(appmod._now_dt() - timedelta(days=days))


# ── Shape ────────────────────────────────────────────────────────────────────

def test_empty_queue_still_reports_the_target(client):
    signup(client)
    q = client.get('/api/queue/today').get_json()
    assert q['target'] == appmod.DAILY_TARGET
    assert q['due'] == [] and q['new'] == []
    assert q['done_today'] == 0 and q['remaining'] == appmod.DAILY_TARGET


def test_config_exposes_target_and_cooldown(client):
    signup(client)
    cfg = client.get('/api/config').get_json()
    assert cfg['daily_target'] == appmod.DAILY_TARGET
    assert cfg['cooldown_days'] == appmod.COOLDOWN_DAYS


def test_new_prospects_fill_the_queue(client):
    signup(client)
    _import(client, _prospects(5))
    q = client.get('/api/queue/today').get_json()
    assert len(q['new']) == 5


def test_queue_is_capped_at_the_target(client):
    signup(client)
    _import(client, _prospects(12))
    q = client.get('/api/queue/today?target=5').get_json()
    assert len(q['new']) == 5


def test_best_fit_prospects_come_first(client):
    signup(client)
    _import(client, [{'company': 'Low', 'license_no': 'L-1', 'icp_score': 1},
                     {'company': 'High', 'license_no': 'L-2', 'icp_score': 6},
                     {'company': 'Mid', 'license_no': 'L-3', 'icp_score': 3}])
    names = [l['company'] for l in client.get('/api/queue/today').get_json()['new']]
    assert names == ['High', 'Mid', 'Low']


# ── Re-touches vs net-new ────────────────────────────────────────────────────

def test_due_tasks_appear_and_count_against_the_target(client):
    """Cadence re-touches are half the point; they must consume the day's number."""
    signup(client)
    lead = new_lead(client, first_name='Jane', last_name='Doe')
    client.post(f"/api/leads/{lead['id']}/tasks",
                json={'kind': 'call', 'title': 'Call #1', 'due_at': _ago(0)})
    _import(client, _prospects(10))
    q = client.get('/api/queue/today?target=4').get_json()
    assert len(q['due']) == 1
    assert q['due'][0]['name'] == 'Jane Doe'
    assert len(q['new']) == 3            # topped up to 4, not 4 on top of the task


def test_a_lead_with_an_open_task_is_not_also_a_new_card(client):
    signup(client)
    got = _import(client, _prospects(1))
    lid = got['details'][0]['lead_id']
    client.post(f'/api/leads/{lid}/tasks', json={'kind': 'call', 'due_at': _ago(0)})
    q = client.get('/api/queue/today').get_json()
    assert len(q['due']) == 1 and q['new'] == []


def test_work_already_done_today_shrinks_the_queue(client):
    signup(client)
    _import(client, _prospects(10))
    q = client.get('/api/queue/today?target=5').get_json()
    client.post(f"/api/leads/{q['new'][0]['id']}/activities", json={'kind': 'call'})
    after = client.get('/api/queue/today?target=5').get_json()
    assert after['done_today'] == 1
    assert after['remaining'] == 4
    assert len(after['new']) == 4


def test_future_tasks_are_not_due_today(client):
    signup(client)
    lead = new_lead(client)
    client.post(f"/api/leads/{lead['id']}/tasks",
                json={'kind': 'call', 'due_at': _ago(-5)})
    assert client.get('/api/queue/today').get_json()['due'] == []


# ── The rules that keep it trustworthy ───────────────────────────────────────

def test_opted_out_leads_never_surface(client):
    signup(client)
    got = _import(client, _prospects(3))
    _set(got['details'][0]['lead_id'], dnc=1)
    q = client.get('/api/queue/today').get_json()
    assert len(q['new']) == 2


def test_opting_out_removes_a_lead_from_a_running_cadence(client):
    signup(client)
    lead = new_lead(client, email='jane@acme.com')
    client.post(f"/api/leads/{lead['id']}/tasks", json={'kind': 'call', 'due_at': _ago(0)})
    assert len(client.get('/api/queue/today').get_json()['due']) == 1
    client.post('/api/suppressions', json={'kind': 'email', 'value': 'jane@acme.com'})
    assert client.get('/api/queue/today').get_json()['due'] == []


def test_a_domain_blocked_today_drops_leads_imported_last_week(client):
    """Suppression is re-checked at queue time, not only at import time."""
    signup(client)
    _import(client, [{'company': 'Acme', 'license_no': 'L-1', 'website': 'acme.com'}])
    assert len(client.get('/api/queue/today').get_json()['new']) == 1
    client.post('/api/suppressions', json={'kind': 'domain', 'value': 'www.acme.com'})
    assert client.get('/api/queue/today').get_json()['new'] == []


def test_recently_touched_partners_are_left_alone(client):
    signup(client)
    got = _import(client, _prospects(3))
    _set(got['details'][0]['lead_id'], last_activity_at=_ago(2))
    assert len(client.get('/api/queue/today').get_json()['new']) == 2


def test_the_cooldown_expires(client):
    signup(client)
    got = _import(client, _prospects(1))
    _set(got['details'][0]['lead_id'],
         last_activity_at=_ago(appmod.COOLDOWN_DAYS + 1))
    assert len(client.get('/api/queue/today').get_json()['new']) == 1


# ── Visibility ───────────────────────────────────────────────────────────────

def test_a_rep_cannot_read_another_reps_queue(client):
    signup(client, 'luke')
    signup(client, 'bryan')
    assert client.get('/api/queue/today?rep=luke').status_code == 403


def test_a_manager_can_read_any_queue(client):
    signup(client, 'luke')
    signup(client, 'bryan')
    login(client, 'luke')
    r = client.get('/api/queue/today?rep=bryan')
    assert r.status_code == 200 and r.get_json()['rep'] == 'bryan'


# ── Assignment ───────────────────────────────────────────────────────────────

def test_assign_spreads_prospects_across_reps(client):
    signup(client, 'luke')
    signup(client, 'bryan')
    signup(client, 'derik')
    login(client, 'luke')
    _import(client, _prospects(6))                      # lands on luke
    res = client.post('/api/queue/assign', json={}).get_json()
    assert res['moved'] == 6
    assert res['per_rep'] == {'bryan': 3, 'derik': 3}
    assert client.get('/api/queue/today?rep=luke').get_json()['new'] == []
    assert len(client.get('/api/queue/today?rep=bryan').get_json()['new']) == 3


def test_assign_dry_run_moves_nothing(client):
    signup(client, 'luke')
    signup(client, 'bryan')
    login(client, 'luke')
    _import(client, _prospects(4))
    res = client.post('/api/queue/assign', json={'dry_run': True}).get_json()
    assert res['moved'] == 4
    assert len(client.get('/api/queue/today?rep=luke').get_json()['new']) == 4


def test_assign_leaves_worked_leads_where_they_are(client):
    """A rep must not lose a partner they've already spoken to."""
    signup(client, 'luke')
    signup(client, 'bryan')
    login(client, 'luke')
    got = _import(client, _prospects(3))
    worked = got['details'][0]['lead_id']
    client.post(f'/api/leads/{worked}/activities', json={'kind': 'call'})
    res = client.post('/api/queue/assign', json={}).get_json()
    assert res['moved'] == 2
    with appmod.get_db() as db:
        assert db.execute('SELECT rep FROM leads WHERE id=?', (worked,)).fetchone()['rep'] == 'luke'


def test_assign_ignores_hand_entered_leads(client):
    """Only imported prospects get handed out; a rep's own lead stays theirs."""
    signup(client, 'luke')
    signup(client, 'bryan')
    login(client, 'luke')
    new_lead(client, first_name='Mine')
    assert client.post('/api/queue/assign', json={}).get_json()['moved'] == 0


def test_assign_rejects_unknown_reps(client):
    signup(client)
    r = client.post('/api/queue/assign', json={'reps': ['ghost']})
    assert r.status_code == 400


def test_assign_is_manager_only(client):
    signup(client, 'luke')
    signup(client, 'bryan')
    assert client.post('/api/queue/assign', json={}).status_code == 403
