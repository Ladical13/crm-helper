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
    # A new lead now arrives already enrolled, so it carries its own first
    # cadence task; this adds a second, manual one.
    client.post(f"/api/leads/{lead['id']}/tasks",
                json={'kind': 'call', 'title': 'Call #1', 'due_at': _ago(0)})
    _import(client, _prospects(10))
    q = client.get('/api/queue/today?target=4').get_json()
    assert len(q['due']) == 2
    assert {d['name'] for d in q['due']} == {'Jane Doe'}
    assert len(q['new']) == 2            # topped up to 4, not 4 on top of the tasks


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
    # Clear the task the automatic enrollment just created, so the only thing
    # left is the one scheduled five days out.
    with appmod.get_db() as db:
        db.execute('UPDATE tasks SET done=1 WHERE lead_id=?', (lead['id'],))
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
    # Two: the automatic cadence step and the manual follow-up above.
    assert len(client.get('/api/queue/today').get_json()['due']) == 2
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


# ── POST /api/queue/log ──────────────────────────────────────────────────────

def test_queue_log_completes_the_task_and_moves_the_cadence_on(client):
    signup(client)
    lead = new_lead(client)                    # homeowners enrol in a cadence
    with appmod.get_db() as db:
        task = db.execute('SELECT * FROM tasks WHERE lead_id=? AND done=0',
                          (lead['id'],)).fetchone()
    assert task, 'expected the cadence to have created a first task'
    r = client.post('/api/queue/log', json={'lead_id': lead['id'], 'kind': 'call',
                                            'task_id': task['id']})
    assert r.status_code == 201
    assert r.get_json()['rep'] == 'luke'       # a human is credited as themselves
    assert r.get_json()['done_today'] == 1
    with appmod.get_db() as db:
        assert db.execute('SELECT done FROM tasks WHERE id=?', (task['id'],)).fetchone()[0] == 1
        # The cadence materialised its next step.
        assert db.execute('SELECT COUNT(*) FROM tasks WHERE lead_id=? AND done=0',
                          (lead['id'],)).fetchone()[0] >= 1


def test_queue_log_refuses_a_task_from_another_lead(client):
    signup(client)
    a, b = new_lead(client), new_lead(client)
    with appmod.get_db() as db:
        tb = db.execute('SELECT id FROM tasks WHERE lead_id=?', (b['id'],)).fetchone()
    r = client.post('/api/queue/log', json={'lead_id': a['id'], 'kind': 'call',
                                            'task_id': tb['id']})
    assert r.status_code == 400


def test_queue_log_hides_other_reps_leads_from_a_rep(client):
    signup(client)                              # luke, admin
    lead = new_lead(client)
    signup(client, 'bryan')                     # a rep, now signed in
    r = client.post('/api/queue/log', json={'lead_id': lead['id'], 'kind': 'call'})
    assert r.status_code == 404


def test_a_partner_marked_do_not_contact_drops_out_of_re_touches(client):
    """The opt-out status kept cadence tasks alive, so someone who said "stop"
    reappeared in the due list. The column-level dnc flag already excluded them;
    the outreach status now does too."""
    signup(client)
    lead = new_lead(client)
    with appmod.get_db() as db:
        db.execute("UPDATE tasks SET due_at=? WHERE lead_id=?", (_ago(1), lead['id']))
    due_ids = lambda: [d['lead_id'] for d in client.get('/api/queue/today').get_json()['due']]
    assert lead['id'] in due_ids()
    client.patch(f"/api/leads/{lead['id']}/outreach-status", json={'status': 'dnc'})
    assert lead['id'] not in due_ids()


# ── One card, one side of the day ────────────────────────────────────────────

def _mixed(client):
    """Three fresh prospects (two with an address) and two scheduled touches."""
    _import(client, [
        {'company': 'Email HOA', 'license_no': 'E-1', 'email': 'board@emailhoa.org'},
        {'company': 'Both HOA', 'license_no': 'B-1', 'email': 'mgr@bothhoa.org',
         'phone': '970-555-0111'},
        {'company': 'Phone HOA', 'license_no': 'P-1', 'phone': '970-555-0112'},
    ])
    a = new_lead(client, first_name='Ann', email='ann@example.com', phone='970-555-0113')
    b = new_lead(client, first_name='Bo', email='bo@example.com', phone='970-555-0114')
    with appmod.get_db() as db:      # replace the cadence's own first task
        db.execute('DELETE FROM tasks')
    client.post(f"/api/leads/{a['id']}/tasks", json={'kind': 'email', 'due_at': _ago(0)})
    client.post(f"/api/leads/{b['id']}/tasks", json={'kind': 'call', 'due_at': _ago(0)})
    return a, b


def _ids(q):
    return {d['lead_id'] for d in q['due']} | {n['id'] for n in q['new']}


def test_no_card_is_on_both_sides_of_the_day(client):
    """Jarvis drafting an email to someone the rep is also about to dial is the
    one thing the split exists to prevent."""
    signup(client)
    _mixed(client)
    email = client.get('/api/queue/today?channel=email').get_json()
    phone = client.get('/api/queue/today?channel=phone').get_json()
    everything = client.get('/api/queue/today?channel=all').get_json()
    assert not _ids(email) & _ids(phone)
    assert _ids(email) | _ids(phone) == _ids(everything)
    assert {n['company'] for n in email['new']} == {'Email HOA', 'Both HOA'}
    assert {n['company'] for n in phone['new']} == {'Phone HOA'}
    assert [d['first_name'] for d in email['due']] == ['Ann']
    assert [d['first_name'] for d in phone['due']] == ['Bo']


def test_a_step_falls_to_the_channel_we_can_actually_use():
    assert appmod._card_channel('email', 'a@b.com', '') == 'email'
    assert appmod._card_channel('email', '', '970-555-0100') == 'phone'
    assert appmod._card_channel('call', 'a@b.com', '') == 'email'
    assert appmod._card_channel('text', 'a@b.com', '970-555-0100') == 'phone'
    assert appmod._card_channel('meeting', '', '') == 'phone'


def test_without_the_setting_the_queue_is_one_list(client):
    signup(client)
    _mixed(client)
    q = client.get('/api/queue/today').get_json()
    assert q['channel'] == 'all' and 'day' not in q
    assert len(q['due']) == 2 and len(q['new']) == 3


def test_a_rep_jarvis_drafts_for_is_handed_the_phone_side(client, monkeypatch):
    monkeypatch.setattr(appmod, 'JARVIS_EMAIL_REPS', {'luke': 10})
    signup(client)
    _mixed(client)
    q = client.get('/api/queue/today').get_json()
    assert q['channel'] == 'phone'
    assert q['target'] == appmod.DAILY_TARGET - 10
    assert {n['company'] for n in q['new']} == {'Phone HOA'}
    assert q['day'] == {'target': appmod.DAILY_TARGET, 'done': 0,
                        'email_target': 10, 'email_done': 0}
    # Jarvis asks for its share by name.
    assert client.get('/api/queue/today?channel=email').get_json()['target'] == 10
    # Research mode is not a channel: it still lists who needs a contact found.
    assert client.get('/api/queue/today?contact=research').get_json()['channel'] == 'all'


def test_emails_sent_do_not_use_up_the_phone_side(client, monkeypatch):
    monkeypatch.setattr(appmod, 'JARVIS_EMAIL_REPS', {'luke': 2})
    signup(client)
    a, b = _mixed(client)
    client.post('/api/queue/log', json={'lead_id': a['id'], 'kind': 'email'})
    phone = client.get('/api/queue/today').get_json()
    assert phone['done_today'] == 0
    assert phone['day']['email_done'] == 1 and phone['day']['done'] == 1
    assert client.get('/api/queue/today?channel=email').get_json()['done_today'] == 1


def test_an_unknown_channel_is_refused(client):
    signup(client)
    assert client.get('/api/queue/today?channel=fax').status_code == 400


def test_parse_email_reps():
    assert appmod._parse_email_reps('luke:40, Bryan') == {'luke': 40, 'bryan': 40}
    assert appmod._parse_email_reps('luke:lots') == {'luke': 40}
    assert appmod._parse_email_reps('') == {}


# ── A logged touch books the next one ────────────────────────────────────────

def test_an_email_jarvis_logs_books_the_follow_up_call(client):
    """Logged without an outcome, a card worked through Gmail was never seen
    again: nothing was booked and it came back as 'new' a week later."""
    signup(client)
    lid = _import(client, [{'company': 'Email HOA', 'license_no': 'E-1',
                            'email': 'board@emailhoa.org'}])['details'][0]['lead_id']
    body = {'lead_id': lid, 'kind': 'email', 'outcome': 'emailed', 'ref': 'gmail-abc'}
    r = client.post('/api/queue/log', json=body)
    assert r.status_code == 201 and r.get_json()['logged'] is True
    with appmod.get_db() as db:
        tasks = [dict(t) for t in db.execute('SELECT * FROM tasks WHERE lead_id=? AND done=0', (lid,))]
        lead = db.execute('SELECT outreach_status FROM leads WHERE id=?', (lid,)).fetchone()
    assert [t['kind'] for t in tasks] == ['call']
    assert _ago(-4) > tasks[0]['due_at'] > _ago(-2)
    assert lead['outreach_status'] == 'messaged'
    # Scanning Sent a second time changes nothing.
    again = client.post('/api/queue/log', json=body).get_json()
    assert again['duplicate'] is True
    with appmod.get_db() as db:
        assert db.execute('SELECT COUNT(*) FROM tasks WHERE lead_id=?', (lid,)).fetchone()[0] == 1


def test_a_callback_logged_through_the_queue_needs_its_date(client):
    signup(client)
    lead = new_lead(client)
    base = {'lead_id': lead['id'], 'kind': 'call', 'outcome': 'callback'}
    assert client.post('/api/queue/log', json=base).status_code == 400
    assert client.post('/api/queue/log', json=dict(base, follow_up_at='2027-01-05')).status_code == 201


def test_jarvis_cannot_log_an_appointment(client, monkeypatch):
    """An appointment is booked by a person on a calendar."""
    signup(client)
    lead = new_lead(client)
    monkeypatch.setattr(appmod.papibot, 'is_apibot', lambda username=None: True)
    # Mounted, this route is on the portal's write list as /crm/api/queue/log;
    # standalone it has no prefix, so let the portal's guard through and test
    # the CRM's own rule.
    monkeypatch.setattr(appmod.papibot, 'write_allowed', lambda method, path: True)
    r = client.post('/api/queue/log', json={'lead_id': lead['id'], 'kind': 'call',
                                            'outcome': 'appt_set'})
    assert r.status_code == 403 and 'appt_set' in r.get_json()['error']
    ok = client.post('/api/queue/log', json={'lead_id': lead['id'], 'kind': 'call',
                                             'outcome': 'left_vm'})
    assert ok.status_code == 201 and ok.get_json()['rep'] == 'luke'
