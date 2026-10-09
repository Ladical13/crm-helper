"""The bookkeeping behind a hundred touches a day.

Jarvis drafts the emails and a person sends them, so somebody has to remember
which drafts are waiting, which were sent, what came back and whose day it all
counts toward. That used to be a file in Jarvis's own memory. It is the CRM's
now, and these are the rules that make it safe to lean on: nobody is drafted
twice, a reply does what the same answer does on the card, the day is one
number however it is split, and working another rep's list moves no lead.
"""
from datetime import timedelta

import app as appmod
from conftest import signup, login, new_lead


def _import(client, rows, **kw):
    body = {'rows': rows, 'lead_type': 'hoa', 'source': 'dora'}
    body.update(kw)
    return client.post('/api/prospects/import', json=body).get_json()


def _set(lead_id, **cols):
    sets = ', '.join(f'{k}=?' for k in cols)
    with appmod.get_db() as db:
        db.execute(f'UPDATE leads SET {sets} WHERE id=?', list(cols.values()) + [lead_id])


def _ago(days):
    return appmod._iso(appmod._now_dt() - timedelta(days=days))


def _ids(q):
    return {d['lead_id'] for d in q['due']} | {n['id'] for n in q['new']}


def _queue(client, channel='all', **kw):
    qs = '&'.join(f'{k}={v}' for k, v in dict(kw, channel=channel).items())
    return client.get('/api/queue/today?' + qs).get_json()


def _email_lead(client, n=1, **kw):
    """One imported prospect with an address; returns its lead id."""
    row = {'company': f'Email HOA {n}', 'license_no': f'E-{n}',
           'email': f'board{n}@emailhoa.org'}
    return _import(client, [row], **kw)['details'][0]['lead_id']


def _draft(client, lead_id, **kw):
    return client.post('/api/queue/drafts', json=dict({'lead_id': lead_id}, **kw))


def _sent_draft(client, lid):
    d = _draft(client, lid).get_json()
    client.post('/api/queue/log', json={'lead_id': lid, 'kind': 'email', 'outcome': 'emailed',
                                        'draft_id': d['id'], 'ref': 'm-' + lid})
    return d


def _reply(client, draft, reply, **kw):
    return client.patch(f"/api/queue/drafts/{draft['id']}", json=dict({'reply': reply}, **kw))


def _open_tasks(lead_id):
    with appmod.get_db() as db:
        return [dict(t) for t in db.execute(
            'SELECT * FROM tasks WHERE lead_id=? AND done=0', (lead_id,))]


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


# ── The draft ledger ─────────────────────────────────────────────────────────

def test_a_lead_with_a_draft_waiting_is_off_the_queue(client):
    """The ledger is the lock. As a list in Jarvis's memory, the morning that
    file was lost the same people were drafted again."""
    signup(client)
    lid = _email_lead(client)
    assert lid in _ids(_queue(client, 'email'))
    r = _draft(client, lid, subject='Roof condition report', draft_ref='r-1')
    assert r.status_code == 201
    d = r.get_json()
    assert d['status'] == 'pending' and d['rep'] == 'luke'
    assert d['recipient'] == 'board1@emailhoa.org'          # filled in from the lead
    for channel in ('email', 'phone', 'all'):
        assert lid not in _ids(_queue(client, channel))
    # A second run that finds the lead reserved is refused, not handed a copy.
    again = _draft(client, lid)
    assert again.status_code == 409 and again.get_json()['draft']['id'] == d['id']


def test_logging_the_send_closes_the_draft_and_books_the_call(client):
    signup(client)
    lid = _email_lead(client)
    d = _draft(client, lid).get_json()
    body = {'lead_id': lid, 'kind': 'email', 'outcome': 'emailed', 'draft_id': d['id'],
            'ref': 'gmail-msg-1', 'thread_ref': 'gmail-thread-1'}
    assert client.post('/api/queue/log', json=body).status_code == 201
    sent = client.get('/api/queue/drafts?status=sent').get_json()
    assert [(s['message_ref'], s['thread_ref']) for s in sent] == [('gmail-msg-1', 'gmail-thread-1')]
    assert client.get('/api/queue/drafts?status=pending').get_json() == []
    assert [t['kind'] for t in _open_tasks(lid)] == ['call']
    # Scanning Sent again counts nothing twice.
    assert client.post('/api/queue/log', json=body).get_json()['duplicate'] is True
    assert _queue(client, 'email')['done_today'] == 1


def test_a_touch_logged_before_the_ledger_knew_still_closes_the_draft(client):
    signup(client)
    lid = _email_lead(client)
    d = _draft(client, lid).get_json()
    body = {'lead_id': lid, 'kind': 'email', 'outcome': 'emailed', 'ref': 'gmail-msg-2'}
    client.post('/api/queue/log', json=body)                 # no draft_id: draft still waits
    assert len(client.get('/api/queue/drafts?status=pending').get_json()) == 1
    r = client.post('/api/queue/log', json=dict(body, draft_id=d['id'])).get_json()
    assert r['duplicate'] is True
    assert client.get('/api/queue/drafts?status=pending').get_json() == []
    assert _queue(client, 'email')['done_today'] == 1


def test_a_draft_is_only_sent_by_logging_the_touch(client):
    """Marked sent by hand it would start no cooldown and book no follow-up."""
    signup(client)
    d = _draft(client, _email_lead(client)).get_json()
    assert client.patch(f"/api/queue/drafts/{d['id']}", json={'status': 'sent'}).status_code == 400


def test_a_draft_given_up_on_puts_the_card_back(client):
    signup(client)
    lid = _email_lead(client)
    d = _draft(client, lid).get_json()
    assert client.patch(f"/api/queue/drafts/{d['id']}", json={'status': 'expired'}).status_code == 200
    assert lid in _ids(_queue(client, 'email'))
    assert _draft(client, lid).status_code == 201          # and may be drafted afresh


def test_the_queue_stops_waiting_on_a_draft_nobody_came_back_for(client):
    """Jarvis not running for a week must not park a lead for good."""
    signup(client)
    lid = _email_lead(client)
    d = _draft(client, lid).get_json()
    with appmod.get_db() as db:
        db.execute('UPDATE outreach_drafts SET created_at=? WHERE id=?',
                   (_ago(appmod.DRAFT_STALE_DAYS + 0.5), d['id']))
    assert client.get('/api/queue/drafts?status=pending').get_json()[0]['stale'] is True
    assert lid not in _ids(_queue(client, 'email'))          # time to give up, but still held
    with appmod.get_db() as db:
        db.execute('UPDATE outreach_drafts SET created_at=? WHERE id=?',
                   (_ago(appmod.DRAFT_HOLD_DAYS + 0.5), d['id']))
    assert lid in _ids(_queue(client, 'email'))


def test_nobody_who_opted_out_or_has_no_address_gets_a_draft(client):
    signup(client)
    gone = _email_lead(client, 1)
    _set(gone, dnc=1)
    assert _draft(client, gone).status_code == 409
    silent = _import(client, [{'company': 'Phone HOA', 'license_no': 'P-9',
                               'phone': '970-555-0190'}])['details'][0]['lead_id']
    assert _draft(client, silent).status_code == 409
    blocked = _email_lead(client, 2)
    client.post('/api/suppressions', json={'kind': 'domain', 'value': 'emailhoa.org'})
    assert _draft(client, blocked).status_code == 409


# ── Replies ──────────────────────────────────────────────────────────────────

def test_not_interested_by_email_cancels_the_call_that_email_booked(client):
    """The reply used to set a status and nothing else, so three days later
    the rep rang someone who had already said no."""
    signup(client)
    lid = _email_lead(client)
    d = _sent_draft(client, lid)
    assert [t['kind'] for t in _open_tasks(lid)] == ['call']
    r = _reply(client, d, 'not_interested')
    assert r.status_code == 200 and r.get_json()['reply'] == 'not_interested'
    assert _open_tasks(lid) == []
    with appmod.get_db() as db:
        lead = db.execute('SELECT stage, outreach_status FROM leads WHERE id=?', (lid,)).fetchone()
    assert (lead['stage'], lead['outreach_status']) == ('lost', 'not_interested')
    # A reply is not a touch: the day's count is the one email, not two.
    assert _queue(client)['done_today'] == 1


def test_an_interested_reply_puts_the_call_on_today(client):
    signup(client)
    lid = _email_lead(client)
    d = _sent_draft(client, lid)
    _reply(client, d, 'interested')
    assert [(x['lead_id'], x['title']) for x in _queue(client)['due']] == \
        [(lid, 'Book the appointment')]
    # Reading the same thread on the next sweep books nothing more.
    _reply(client, d, 'interested')
    assert len(_queue(client)['due']) == 1


def test_a_stop_reply_suppresses_the_address_for_good(client):
    """On the suppression list, not just on the lead: the same person arriving
    next month from a different dataset has to be stopped at import."""
    signup(client)
    lid = _email_lead(client)
    d = _sent_draft(client, lid)
    _reply(client, d, 'dnc')
    with appmod.get_db() as db:
        lead = db.execute('SELECT dnc, outreach_status FROM leads WHERE id=?', (lid,)).fetchone()
    assert (lead['dnc'], lead['outreach_status']) == (1, 'dnc')
    assert _open_tasks(lid) == []
    again = _import(client, [{'company': 'Same People LLC', 'license_no': 'Z-1',
                              'email': 'board1@emailhoa.org'}])
    assert again['counts']['suppressed'] == 1


def test_a_reply_needs_a_sent_draft_and_a_callback_needs_its_day(client):
    signup(client)
    lid = _email_lead(client)
    d = _draft(client, lid).get_json()
    assert _reply(client, d, 'interested').status_code == 409
    client.post('/api/queue/log', json={'lead_id': lid, 'kind': 'email', 'outcome': 'emailed',
                                        'draft_id': d['id'], 'ref': 'm-1'})
    assert _reply(client, d, 'appt_set').status_code == 400      # booked by a person
    assert _reply(client, d, 'callback').status_code == 400
    assert _reply(client, d, 'callback', follow_up_at='2027-01-05').status_code == 200


# ── A rep's plan ─────────────────────────────────────────────────────────────

def _plan(client, rep, **kw):
    return client.put(f'/api/outreach/plan/{rep}', json=kw)


def test_with_no_plan_the_environment_answers(client, monkeypatch):
    monkeypatch.setattr(appmod, 'JARVIS_EMAIL_REPS', {'luke': 10})
    signup(client)
    p = client.get('/api/outreach/plan').get_json()
    assert (p['daily_target'], p['email_share'], p['covers'], p['saved']) == \
        (appmod.DAILY_TARGET, 10, [], False)


def test_the_phone_side_is_whatever_the_emails_leave(client):
    """The share ramps 15, 25, 40 and the day stays the same number."""
    signup(client)
    assert _plan(client, 'luke', daily_target=100, email_share=15).status_code == 200
    q = client.get('/api/queue/today').get_json()
    assert q['channel'] == 'phone' and q['target'] == 85
    assert q['day'] == {'target': 100, 'done': 0, 'email_target': 15, 'email_done': 0}
    _plan(client, 'luke', email_share=40)
    assert client.get('/api/queue/today').get_json()['target'] == 60
    assert _queue(client, 'email')['target'] == 40


def test_a_plan_with_no_share_is_one_list(client, monkeypatch):
    """A saved zero outranks the environment: this rep works every card
    themselves and none of it is drafted for them."""
    monkeypatch.setattr(appmod, 'JARVIS_EMAIL_REPS', {'luke': 10})
    signup(client)
    _mixed(client)
    _plan(client, 'luke', daily_target=100, email_share=0)
    q = client.get('/api/queue/today').get_json()
    assert q['channel'] == 'all' and q['target'] == 100 and 'day' not in q
    assert len(q['due']) == 2 and len(q['new']) == 3


def test_a_plan_is_bounded_and_only_a_manager_sets_it(client, monkeypatch):
    signup(client)
    signup(client, 'derik')
    assert _plan(client, 'derik', daily_target=100).status_code == 403     # signed in as derik
    login(client, 'luke')
    assert _plan(client, 'luke', email_share=appmod.EMAIL_SHARE_MAX + 1).status_code == 400
    assert _plan(client, 'luke', daily_target=0).status_code == 400
    assert _plan(client, 'nobody', daily_target=50).status_code == 404
    assert _plan(client, 'derik', covers=['luke']).status_code == 400      # a rep cannot cover
    assert {p['rep'] for p in client.get('/api/outreach/plan?all=1').get_json()} == {'luke', 'derik'}
    # Jarvis recommends moving the ramp; a person moves it.
    monkeypatch.setattr(appmod.papibot, 'is_apibot', lambda username=None: True)
    assert _plan(client, 'luke', email_share=25).status_code == 403


# ── Covering another rep's list ──────────────────────────────────────────────

def _derik_has_a_list(client):
    """luke manages; derik owns one emailable prospect and one call due today."""
    signup(client)
    signup(client, 'derik')
    theirs = new_lead(client, first_name='Dee', phone='970-555-0140')
    with appmod.get_db() as db:      # one plain follow-up, not the cadence a new lead starts
        db.execute('DELETE FROM tasks')
        db.execute('DELETE FROM cadence_enrollments')
    client.post(f"/api/leads/{theirs['id']}/tasks", json={'kind': 'call', 'due_at': _ago(0)})
    login(client, 'luke')
    cold = _email_lead(client, 7, assign='derik')
    return theirs['id'], cold


def test_covering_serves_the_other_reps_cards_as_one_queue(client):
    """Nothing is reassigned: the cards arrive in the manager's queue, signed
    by the manager, and the follow-up stays with the lead's owner."""
    theirs, cold = _derik_has_a_list(client)
    assert _ids(_queue(client)) == set()
    assert _plan(client, 'luke', covers=['derik']).status_code == 200
    q = _queue(client)
    assert _ids(q) == {theirs, cold} and q['covers'] == ['derik']
    assert {c['owner'] for c in q['due'] + q['new']} == {'derik'}
    signed = q['new'][0]['draft']['body']
    assert 'Luke' in signed and 'Derik' not in signed

    # Luke makes derik's call. It is Luke's touch and derik's follow-up.
    client.post(f'/api/leads/{theirs}/outcome',
                json={'outcome': 'left_vm', 'task_id': q['due'][0]['id']})
    assert _queue(client)['done_today'] == 1
    with appmod.get_db() as db:
        owner = db.execute('SELECT rep FROM leads WHERE id=?', (theirs,)).fetchone()['rep']
    assert owner == 'derik' and [t['rep'] for t in _open_tasks(theirs)] == ['derik']

    # Take the cover off and the list is back where it was.
    _plan(client, 'luke', covers=[])
    assert _ids(_queue(client)) == set()
    login(client, 'derik')
    assert cold in _ids(client.get('/api/queue/today').get_json())


def test_a_draft_written_under_cover_is_the_senders_touch(client, monkeypatch):
    theirs, cold = _derik_has_a_list(client)
    # Without the cover, luke's Gmail has no business holding derik's draft.
    assert _draft(client, cold, rep='luke').status_code == 400
    _plan(client, 'luke', covers=['derik'])
    monkeypatch.setattr(appmod.papibot, 'is_apibot', lambda username=None: True)
    monkeypatch.setattr(appmod.papibot, 'write_allowed', lambda method, path: True)
    d = _draft(client, cold, rep='luke')
    assert d.status_code == 201 and d.get_json()['rep'] == 'luke'
    r = client.post('/api/queue/log', json={'lead_id': cold, 'kind': 'email', 'outcome': 'emailed',
                                            'draft_id': d.get_json()['id'], 'ref': 'm-cover'})
    assert r.get_json()['rep'] == 'luke'
    assert [(t['rep'], t['kind']) for t in _open_tasks(cold)] == [('derik', 'call')]


def test_a_demoted_manager_loses_the_list_they_covered(client):
    """The role is read on every request, not trusted from the saved row."""
    theirs, cold = _derik_has_a_list(client)
    signup(client, 'casey')
    login(client, 'luke')
    appmod.pusers.set_role('casey', 'manager')
    _plan(client, 'casey', covers=['derik'])
    assert _ids(_queue(client, rep='casey')) == {theirs, cold}
    appmod.pusers.set_role('casey', 'rep')
    assert _ids(_queue(client, rep='casey')) == set()


# ── The scorecard ────────────────────────────────────────────────────────────

def test_the_streak_skips_weekends_and_does_not_break_before_the_day_is_over():
    def day(weekday, total):
        return {'weekday': weekday, 'total': total}
    # Newest first: today (nothing yet), a weekend, then three good weekdays.
    assert appmod._streak([day(True, 0), day(False, 0), day(False, 0),
                           day(True, 9), day(True, 7), day(True, 5), day(True, 1)], 5) == 3
    assert appmod._streak([day(True, 5), day(True, 5), day(True, 4), day(True, 5)], 5) == 2
    assert appmod._streak([day(True, 0), day(True, 0), day(True, 9)], 5) == 0


def test_scorecard_counts_the_day_and_what_the_emails_earned(client):
    signup(client)
    _plan(client, 'luke', daily_target=2, email_share=1)
    a, b = _email_lead(client, 1), _email_lead(client, 2)
    da, db_ = _sent_draft(client, a), _sent_draft(client, b)
    _reply(client, da, 'interested')
    _reply(client, db_, 'bad_contact')
    s = client.get('/api/outreach/scorecard').get_json()
    assert s['today'] == {'done': 2, 'target': 2, 'email_done': 2, 'email_target': 1}
    assert s['days'][0]['hit'] is True
    assert s['streak'] == (1 if s['days'][0]['weekday'] else 0)
    e = s['email']
    assert (e['sent'], e['replies'], e['positive'], e['bounces']) == (2, 1, 1, 1)
    assert e['by_type']['hoa']['first'] == {'sent': 2, 'replies': 1}


def test_supply_is_counted_by_the_queues_own_rule(client):
    """If the two disagreed, supply would read as a week of cards on a morning
    the queue came up empty."""
    signup(client)
    _plan(client, 'luke', daily_target=4, email_share=1)
    _mixed(client)                                # 2 email + 1 phone fresh; 1 email + 1 call due
    _import(client, [{'company': 'Nameplate HOA', 'license_no': 'N-1'}])   # no way to reach them
    _draft(client, _email_lead(client, 3))        # in flight: not supply
    s = client.get('/api/outreach/scorecard').get_json()['supply']
    assert s['fresh'] == {'email': 2, 'phone': 1, 'research': 1}
    assert s['booked_next_week'] == {'email': 1, 'phone': 1}
    assert s['days'] == {'email': 3.0, 'phone': round(2 / 3, 1)}
    assert len(_queue(client, 'email', target=50)['new']) == s['fresh']['email']


def test_a_rep_reads_only_their_own_scorecard_and_drafts(client):
    signup(client)
    signup(client, 'derik')
    assert client.get('/api/outreach/scorecard?rep=luke').status_code == 403
    assert client.get('/api/queue/drafts?rep=luke').status_code == 403
    assert client.get('/api/outreach/plan?rep=luke').status_code == 403
    assert client.get('/api/outreach/scorecard').status_code == 200
    login(client, 'luke')
    assert client.get('/api/outreach/scorecard?rep=derik').get_json()['rep'] == 'derik'
