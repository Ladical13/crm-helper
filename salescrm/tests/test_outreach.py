"""Cold outreach: the template library and the outcome-driven follow-ups.

Two halves, and each has a way to fail quietly:

* **Templates reach a stranger in a rep's voice.** A banned opener, an
  unfilled "{rep_phone}", a text that splits into three messages or a review
  ask to someone who never bought is a first impression nobody gets back. The
  starter set is held to the same rules a manager's edit is, and a manager's
  edit is never overwritten by a restart.
* **Outcomes are the follow-up list.** "No answer" must book the retry,
  "not interested" must stop every automatic touch, and a lead already in a
  cadence must not be booked twice. Each of those is invisible when it breaks —
  the rep just calls the wrong people.
"""
import json
import os
from datetime import timedelta

import pytest

import app as appmod
from conftest import signup, login, new_lead

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _library():
    with open(os.path.join(HERE, 'outreach_library.json'), encoding='utf-8') as f:
        return json.load(f)['templates']


def _tasks(lead_id, done=0):
    with appmod.get_db() as db:
        return [dict(r) for r in db.execute(
            'SELECT * FROM tasks WHERE lead_id=? AND done=? ORDER BY due_at', (lead_id, done))]


def _lead(lead_id):
    with appmod.get_db() as db:
        return dict(db.execute('SELECT * FROM leads WHERE id=?', (lead_id,)).fetchone())


def _outcome(client, lead_id, outcome, **kw):
    return client.post(f'/api/leads/{lead_id}/outcome', json=dict(outcome=outcome, **kw))


def _cold(client, **kw):
    """A prospect as an import leaves it: stage new, no cadence, no task."""
    body = {'rows': [dict({'company': 'Cold Co', 'first_name': 'Pat', 'phone': '970-555-0101',
                           'city': 'Loveland', 'source_ref': 'test:1'}, **kw)],
            'lead_type': kw.pop('lead_type', 'homeowner') if 'lead_type' in kw else 'homeowner',
            'source': 'storm'}
    client.post('/api/prospects/import', json=body)
    with appmod.get_db() as db:
        return db.execute("SELECT id FROM leads ORDER BY created_at DESC LIMIT 1").fetchone()['id']


# ── The starter library ─────────────────────────────────────────────────────

def test_every_starter_template_passes_the_rules_a_manager_is_held_to():
    for t in _library():
        row = dict(t, lead_type=t.get('lead_type', ''), stage=t.get('stage', ''))
        assert appmod._template_problems(row) == [], t['key']


def test_starter_keys_are_unique():
    keys = [t['key'] for t in _library()]
    assert len(keys) == len(set(keys))


@pytest.mark.parametrize('audience', ['homeowner', 'partner', 'commercial', 'past_customer'])
def test_every_audience_has_texts_voicemails_and_emails(client, audience):
    with appmod.get_db() as db:
        chans = {r['channel'] for r in db.execute(
            'SELECT channel FROM templates WHERE audience=? AND archived=0', (audience,))}
    assert chans == {'email', 'text', 'voicemail', 'call'}


def test_first_cold_texts_offer_a_way_out():
    """Someone we have never spoken to gets told how to stop hearing from us."""
    for t in _library():
        if t['channel'] == 'text' and t['step'] == 'first':
            assert 'Reply STOP' in t['body'], t['key']


def test_scripts_never_open_on_a_bare_first_name():
    """Most cold lists have no first name; 'Hi , this is' is the tell."""
    for t in _library():
        if t['channel'] in ('text', 'voicemail'):
            assert not t['body'].startswith(('Hi {first_name}', '{first_name}')), t['key']


def test_seeding_is_idempotent(client):
    with appmod.get_db() as db:
        before = db.execute('SELECT COUNT(*) c FROM templates').fetchone()['c']
    appmod.seed_templates()
    with appmod.get_db() as db:
        assert db.execute('SELECT COUNT(*) c FROM templates').fetchone()['c'] == before


def test_a_managers_edit_survives_a_restart(client):
    signup(client)                                   # first user is the admin
    tid = next(t['id'] for t in client.get('/api/templates').get_json()
               if t['seed_key'] == 'text:homeowner:breakup')
    r = client.put(f'/api/templates/{tid}', json={'body': '{greeting} our last word.'})
    assert r.status_code == 200, r.get_json()
    appmod.seed_templates()
    t = next(t for t in client.get('/api/templates').get_json() if t['id'] == tid)
    assert t['body'] == '{greeting} our last word.'


def test_archiving_a_seeded_template_is_permanent(client):
    signup(client)
    tid = next(t['id'] for t in client.get('/api/templates').get_json()
               if t['seed_key'] == 'text:homeowner:breakup')
    assert client.delete(f'/api/templates/{tid}').status_code == 200
    appmod.seed_templates()
    keys = [t['seed_key'] for t in client.get('/api/templates').get_json()]
    assert 'text:homeowner:breakup' not in keys


# ── Editing ─────────────────────────────────────────────────────────────────

def _new(**kw):
    return dict({'name': 'Test', 'channel': 'text', 'audience': 'homeowner',
                 'step': 'first', 'body': '{greeting} hello from {rep_first}.'}, **kw)


def test_a_manager_can_add_a_template(client):
    signup(client)
    r = client.post('/api/templates', json=_new())
    assert r.status_code == 201


def test_a_rep_cannot_edit_the_library(client):
    signup(client)                                   # admin
    signup(client, 'casey')                          # rep, now signed in
    assert client.post('/api/templates', json=_new()).status_code == 403
    assert client.get('/api/templates').status_code == 200


@pytest.mark.parametrize('bad,why', [
    ({'body': 'Call me at {rep_phone}'}, 'Unknown fill-in'),
    ({'body': 'Just checking in about your roof.'}, 'bulk mail'),
    ({'body': 'x ' * 200}, 'Too long for a text'),
    ({'channel': 'email', 'subject': ''}, 'subject'),
    ({'channel': 'email', 'subject': 's', 'body': 'word ' * 120}, 'under 100 words'),
])
def test_a_template_that_would_embarrass_a_rep_is_refused(client, bad, why):
    signup(client)
    r = client.post('/api/templates', json=_new(**bad))
    assert r.status_code == 400
    assert why in r.get_json()['error']


def test_preview_renders_against_a_real_lead(client):
    signup(client)
    lead = new_lead(client, first_name='Dana', city='Berthoud')
    r = client.post('/api/templates/preview',
                    json=_new(body='{greeting} roofs in {city}.', lead_id=lead['id'])).get_json()
    assert r['rendered']['body'] == 'Hi Dana, roofs in Berthoud.'
    assert r['problems'] == []


# ── Which templates a lead is offered ───────────────────────────────────────

@pytest.mark.parametrize('lead,aud', [
    ({'lead_type': 'homeowner', 'stage': 'new'}, 'homeowner'),
    ({'lead_type': 'realtor', 'stage': 'lost'}, 'partner'),
    ({'lead_type': 'church', 'stage': 'new'}, 'commercial'),
    ({'lead_type': 'homeowner', 'stage': 'won'}, 'past_customer'),
    ({'lead_type': 'homeowner', 'stage': 'new', 'source': 'existing_customer'}, 'past_customer'),
])
def test_audience(lead, aud):
    assert appmod._audience_for(lead) == aud


def test_a_homeowner_is_offered_homeowner_texts_first_touch_recommended(client):
    signup(client)
    lead = new_lead(client, first_name='Dana', city='Loveland')
    m = client.get(f'/api/leads/{lead["id"]}/messages').get_json()
    texts = m['text']['templates']
    assert texts and all(t['audience'] == 'homeowner' for t in texts)
    rec = next(t for t in texts if t['id'] == m['text']['recommended'])
    assert rec['step'] == 'first'
    assert rec['body'].startswith('Hi Dana,')
    assert '\n' not in rec['body']


def test_a_review_ask_never_goes_to_someone_who_did_not_buy(client):
    signup(client)
    lead = new_lead(client)
    client.patch(f'/api/leads/{lead["id"]}/stage', json={'stage': 'lost'})
    names = [t['name'] for t in
             client.get(f'/api/leads/{lead["id"]}/messages').get_json()['text']['templates']]
    assert 'Refresh an old estimate' in names
    assert 'Review request' not in names


def test_emails_carry_the_signature_and_texts_do_not(client):
    signup(client)
    lead = new_lead(client)
    m = client.get(f'/api/leads/{lead["id"]}/messages').get_json()
    assert all('projectoneroofingcolorado.com' in t['body'] for t in m['email']['templates'])
    assert not any('projectoneroofingcolorado.com' in t['body'] for t in m['text']['templates'])


def test_a_managers_edit_reaches_the_queue_draft(client):
    """Editable in the app means the card uses the edit — not the file."""
    signup(client)
    tid = next(t['id'] for t in client.get('/api/templates').get_json()
               if t['seed_key'] == 'email:hoa:first')
    client.put(f'/api/templates/{tid}', json={'subject': 'Board roof report for {company}'})
    lead = new_lead(client, lead_type='hoa', company='Sycamore HOA', first_name='')
    d = client.get(f'/api/leads/{lead["id"]}/draft').get_json()
    assert d['subject'] == 'Board roof report for Sycamore HOA'


# ── Outcomes ────────────────────────────────────────────────────────────────

def test_no_answer_books_a_retry_in_two_days(client):
    signup(client)
    lid = _cold(client)
    r = _outcome(client, lid, 'no_answer').get_json()
    assert r['lead']['outreach_status'] == 'attempted'
    t = r['follow_up']
    due = appmod._now_dt() + timedelta(days=2)
    assert t['due_at'][:10] == appmod._iso(due)[:10]


def test_the_outcome_is_recorded_on_the_activity(client):
    signup(client)
    lid = _cold(client)
    _outcome(client, lid, 'left_vm')
    with appmod.get_db() as db:
        a = db.execute("SELECT kind, outcome FROM activities WHERE lead_id=? AND outcome!=''",
                       (lid,)).fetchone()
    assert (a['kind'], a['outcome']) == ('call', 'left_vm')


def test_a_callback_needs_the_day_and_books_exactly_that(client):
    signup(client)
    lid = _cold(client)
    assert _outcome(client, lid, 'callback').status_code == 400
    r = _outcome(client, lid, 'callback', follow_up_at='2030-03-04T10:30').get_json()
    assert r['follow_up']['due_at'] == '2030-03-04T10:30:00Z'
    assert r['lead']['outreach_status'] == 'callback'


def test_not_interested_closes_the_lead_and_every_automatic_touch(client):
    signup(client)
    lead = new_lead(client)                      # homeowners enrol in the 7-touch
    assert _tasks(lead['id'])
    r = _outcome(client, lead['id'], 'not_interested').get_json()
    assert r['lead']['stage'] == 'lost'
    assert r['lead']['outreach_status'] == 'not_interested'
    assert _tasks(lead['id']) == []
    with appmod.get_db() as db:
        assert not db.execute('SELECT 1 FROM cadence_enrollments WHERE lead_id=? AND active=1',
                              (lead['id'],)).fetchone()


def test_a_lead_in_a_cadence_is_not_double_booked(client):
    """The cadence's next step IS the follow-up."""
    signup(client)
    lead = new_lead(client)
    task = _tasks(lead['id'])[0]
    _outcome(client, lead['id'], 'no_answer', task_id=task['id'])
    open_tasks = _tasks(lead['id'])
    assert len(open_tasks) == 1
    assert open_tasks[0]['enrollment_id']


def test_interested_moves_the_deal_forward_and_asks_for_the_appointment(client):
    signup(client)
    lid = _cold(client)
    r = _outcome(client, lid, 'interested').get_json()
    assert r['lead']['stage'] == 'contacted'
    assert r['follow_up']['title'] == 'Book the appointment'


def test_an_outcome_never_moves_a_deal_backwards(client):
    signup(client)
    lead = new_lead(client)
    client.patch(f'/api/leads/{lead["id"]}/stage', json={'stage': 'estimate_presented'})
    r = _outcome(client, lead['id'], 'talked').get_json()
    assert r['lead']['stage'] == 'estimate_presented'


def test_four_unanswered_in_a_row_parks_the_lead_for_a_month(client):
    signup(client)
    lid = _cold(client)
    for _ in range(appmod.NO_ANSWER_PARK_AFTER - 1):
        _outcome(client, lid, 'no_answer')
    r = _outcome(client, lid, 'no_answer').get_json()
    assert r['lead']['outreach_status'] == 'nurture'
    assert len(_tasks(lid)) == 1
    due = appmod._now_dt() + timedelta(days=appmod.NO_ANSWER_PARK_DAYS)
    assert _tasks(lid)[0]['due_at'][:10] == appmod._iso(due)[:10]


def test_a_wrong_number_leaves_the_cold_queue_and_asks_for_research(client):
    signup(client)
    lid = _cold(client)
    r = _outcome(client, lid, 'wrong_number').get_json()
    assert r['follow_up']['kind'] == 'research'
    _set_task_future(lid)
    fresh = client.get('/api/queue/today').get_json()['new']
    assert lid not in [f['id'] for f in fresh]


def _set_task_future(lid):
    with appmod.get_db() as db:
        db.execute('UPDATE tasks SET done=1 WHERE lead_id=?', (lid,))
        db.execute("UPDATE leads SET last_activity_at='' WHERE id=?", (lid,))


def test_an_unknown_outcome_is_refused(client):
    signup(client)
    lid = _cold(client)
    assert _outcome(client, lid, 'maybe').status_code == 400


def test_a_rep_cannot_log_an_outcome_on_someone_elses_lead(client):
    signup(client)
    lid = _cold(client)
    signup(client, 'casey')
    assert _outcome(client, lid, 'no_answer').status_code == 404


# ── Status board ────────────────────────────────────────────────────────────

def test_the_summary_counts_each_status(client):
    signup(client)
    a, b = _cold(client), _cold(client, source_ref='test:2', phone='970-555-0102')
    _outcome(client, a, 'left_vm')
    s = {r['key']: r for r in client.get('/api/outreach/summary').get_json()}
    assert s['left_vm']['count'] == 1
    assert s['not_contacted']['count'] == 1


def test_the_pipeline_filters_by_status(client):
    signup(client)
    a = _cold(client)
    _cold(client, source_ref='test:2', phone='970-555-0102')
    _outcome(client, a, 'interested')
    ids = [l['id'] for l in client.get('/api/leads?outreach=interested').get_json()]
    assert ids == [a]


def test_do_not_contact_sets_the_status(client):
    signup(client)
    lid = _cold(client)
    client.post('/api/suppressions', json={'kind': 'phone', 'value': '970-555-0101'})
    assert _lead(lid)['outreach_status'] == 'dnc'


def test_the_backfill_tells_the_truth_about_existing_leads(client):
    signup(client)
    touched = new_lead(client)
    lost = new_lead(client)
    client.patch(f'/api/leads/{lost["id"]}/stage', json={'stage': 'lost'})
    with appmod.get_db() as db:
        db.execute("UPDATE leads SET outreach_status='not_contacted', last_activity_at=?",
                   (appmod._now(),))
        appmod._backfill_outreach_status(db)
    assert _lead(touched['id'])['outreach_status'] == 'attempted'
    assert _lead(lost['id'])['outreach_status'] == 'not_interested'


# ── Every partner and commercial type gets its own outreach ──────────────────

TYPED = ('realtor', 'hoa', 'insurance_agent', 'property_manager', 'adjuster',
         'referral_partner', 'gc', 'church', 'school', 'school_district', 'commercial')


@pytest.mark.parametrize('lead_type', TYPED)
def test_each_type_has_its_own_text_voicemail_and_call_script(client, lead_type):
    with appmod.get_db() as db:
        chans = {r['channel'] for r in db.execute(
            'SELECT channel FROM templates WHERE lead_type=? AND archived=0', (lead_type,))}
    assert {'email', 'text', 'voicemail', 'call'} <= chans


def test_no_two_types_share_a_first_text():
    firsts = [t['body'] for t in _library()
              if t['channel'] == 'text' and t['step'] == 'first' and t.get('lead_type')]
    assert len(firsts) == len(set(firsts)) == len(TYPED)


@pytest.mark.parametrize('lead_type', TYPED)
def test_a_types_own_template_is_the_one_recommended(client, lead_type):
    """The HOA's own voicemail, not the generic partner one - even though the
    generic one is marked for this exact touch and the HOA's for any touch."""
    signup(client)
    lead = new_lead(client, lead_type=lead_type, company='Acme', first_name='')
    m = client.get(f'/api/leads/{lead["id"]}/messages').get_json()
    for ch in ('text', 'voicemail', 'call', 'email'):
        rec = next(t for t in m[ch]['templates'] if t['id'] == m[ch]['recommended'])
        with appmod.get_db() as db:
            lt = db.execute('SELECT lead_type FROM templates WHERE id=?', (rec['id'],)).fetchone()[0]
        assert lt == lead_type, (ch, rec['name'])


def test_a_type_without_a_last_text_falls_back_to_the_audiences(client):
    """Three touches in, an HOA with no breakup text of its own gets the
    partner one rather than its own first text again."""
    signup(client)
    lead = new_lead(client, lead_type='hoa', company='Acme HOA', first_name='')
    with appmod.get_db() as db:
        fit = appmod._templates_for(db, dict(_lead(lead['id'])), 'text')
    assert appmod._pick(fit, 'breakup')['name'] == 'Last text'


def test_a_cold_partner_is_never_read_the_past_customer_referral_script():
    """The old SCRIPT_FOR map sent every partner "Glad you're happy with how it
    turned out" - a script for a customer we already served."""
    src = open(os.path.join(HERE, 'static', 'app.js'), encoding='utf-8').read()
    assert 'SCRIPT_FOR[' not in src
    for t in _library():
        if t['channel'] == 'call' and t['audience'] != 'past_customer':
            assert "happy with how it turned out" not in t['body'].lower(), t['key']


# ── Two workers, one seed ───────────────────────────────────────────────────

def _dupe(seed_key, updated_by='seed'):
    with appmod.get_db() as db:
        db.execute('DROP INDEX IF EXISTS tpl_seed_idx')
        r = dict(db.execute('SELECT * FROM templates WHERE seed_key=?', (seed_key,)).fetchone())
        r['id'] = 'dupe-' + seed_key
        r['updated_by'] = updated_by
        r['created_at'] = '2099-01-01T00:00:00Z'
        cols = ','.join(r)
        db.execute(f'INSERT INTO templates ({cols}) VALUES ({",".join("?" * len(r))})',
                   list(r.values()))


def _copies(seed_key):
    with appmod.get_db() as db:
        return [dict(x) for x in db.execute(
            'SELECT id, updated_by FROM templates WHERE seed_key=?', (seed_key,))]


def test_a_template_seeded_twice_is_collapsed_to_one(client):
    """What two gunicorn workers starting together did on the 2026-09-18 deploy."""
    _dupe('text:type:hoa:first')
    assert len(_copies('text:type:hoa:first')) == 2
    appmod.seed_templates()
    assert len(_copies('text:type:hoa:first')) == 1


def test_the_collapse_keeps_a_managers_edited_copy(client):
    _dupe('text:type:hoa:first', updated_by='luke')
    appmod.seed_templates()
    assert [c['id'] for c in _copies('text:type:hoa:first')] == ['dupe-text:type:hoa:first']


def test_the_seed_key_is_unique_once_seeded(client):
    import sqlite3
    appmod.seed_templates()
    with pytest.raises(sqlite3.IntegrityError):
        _dupe_no_drop('text:type:hoa:first')


def _dupe_no_drop(seed_key):
    with appmod.get_db() as db:
        r = dict(db.execute('SELECT * FROM templates WHERE seed_key=?', (seed_key,)).fetchone())
        r['id'] = 'dupe2'
        db.execute(f'INSERT INTO templates ({",".join(r)}) VALUES ({",".join("?" * len(r))})',
                   list(r.values()))


# ── Which template earned the conversation ──────────────────────────────────

def _tid(client, seed_key):
    return next(t['id'] for t in client.get('/api/templates').get_json() if t['seed_key'] == seed_key)


def test_an_outcome_records_the_template_behind_it(client):
    signup(client)
    lid = _cold(client)
    tid = _tid(client, 'text:homeowner:first')
    _outcome(client, lid, 'texted', template_id=tid)
    with appmod.get_db() as db:
        assert db.execute("SELECT template_id FROM activities WHERE outcome='texted'").fetchone()[0] == tid


def test_an_unknown_template_id_is_dropped_not_stored(client):
    signup(client)
    lid = _cold(client)
    _outcome(client, lid, 'texted', template_id='not-a-template')
    with appmod.get_db() as db:
        assert db.execute("SELECT template_id FROM activities WHERE outcome='texted'").fetchone()[0] == ''


def test_the_library_reports_how_each_template_does(client):
    signup(client)
    tid = _tid(client, 'call:homeowner:first')
    a = _cold(client)
    b = _cold(client, source_ref='test:2', phone='970-555-0102')
    _outcome(client, a, 'interested', template_id=tid)
    _outcome(client, b, 'no_answer', template_id=tid)
    t = next(t for t in client.get('/api/templates').get_json() if t['id'] == tid)
    assert (t['used'], t['good']) == (2, 1)


def test_a_drop_by_books_a_call_three_days_later(client):
    signup(client)
    lid = _cold(client, lead_type='church')
    r = _outcome(client, lid, 'dropped_by').get_json()
    assert r['lead']['outreach_status'] == 'visited'
    due = appmod._now_dt() + timedelta(days=3)
    assert r['follow_up']['due_at'][:10] == appmod._iso(due)[:10]
    with appmod.get_db() as db:
        assert db.execute("SELECT kind FROM activities WHERE outcome='dropped_by'").fetchone()[0] == 'door'
