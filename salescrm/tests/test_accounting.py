"""The numbers reps and coaches are judged on.

Every test here pins a figure that was wrong in a way nobody could see from the
screen: the work happened, the tool recorded something, and the something it
recorded was not what the number counted.
"""
from conftest import signup, login, new_lead
import app as appmod


def _task(client, title):
    """The task with this title. A new lead auto-enrols in a cadence, so its
    task list is never just the one the test added."""
    return next(t for t in client.get('/api/tasks').get_json() if t['title'] == title)


def _acts(lead_id, kind=None):
    with appmod.get_db() as db:
        if kind:
            return db.execute('SELECT * FROM activities WHERE lead_id=? AND kind=?',
                              (lead_id, kind)).fetchall()
        return db.execute('SELECT * FROM activities WHERE lead_id=?', (lead_id,)).fetchall()


# ── Completing a task is doing the work ──────────────────────────────────────

def test_completing_a_call_task_counts_as_a_call(client):
    """The bug: this logged kind='note', which is not an outreach kind.

    So working the cadence from My Day left the rep with no outreach on the
    leaderboard, no movement on the daily target, and a lead that still showed
    as stalled -- while the identical call logged from the Outreach tab counted
    in full.
    """
    signup(client)
    lead = new_lead(client)
    client.post(f'/api/leads/{lead["id"]}/tasks',
                json={'kind': 'call', 'title': 'Call #1'})

    client.patch(f'/api/tasks/{_task(client, "Call #1")["id"]}', json={'done': True})

    assert [a['kind'] for a in _acts(lead['id'], 'call')], 'no call was recorded'
    board = client.get('/api/leaderboard').get_json()
    assert board[0]['outreach'] == 1


def test_completing_a_task_clears_the_stall(client):
    """last_activity_at is only bumped for outreach kinds, so the mis-typed
    activity also left the lead looking untouched to the stall detector."""
    signup(client)
    lead = new_lead(client)
    client.post(f'/api/leads/{lead["id"]}/tasks', json={'kind': 'call', 'title': 'Ring them'})
    client.patch(f'/api/tasks/{_task(client, "Ring them")["id"]}', json={'done': True})
    assert client.get(f'/api/leads/{lead["id"]}').get_json()['last_activity_at']


def test_a_reminder_task_stays_a_note(client):
    """Not every task is outreach. A task whose kind isn't a way of reaching a
    human must not inflate the outreach count just by being ticked."""
    signup(client)
    lead = new_lead(client)
    client.post(f'/api/leads/{lead["id"]}/tasks',
                json={'kind': 'todo', 'title': 'Order materials'})
    client.patch(f'/api/tasks/{_task(client, "Order materials")["id"]}', json={'done': True})
    assert [a['body'] for a in _acts(lead['id'], 'note')] == ['✓ Completed: Order materials']
    assert client.get('/api/leaderboard').get_json()[0]['outreach'] == 0


def test_reticking_a_done_task_does_not_log_it_twice(client):
    signup(client)
    lead = new_lead(client)
    client.post(f'/api/leads/{lead["id"]}/tasks', json={'kind': 'call', 'title': 'Ring them'})
    task = _task(client, 'Ring them')
    for _ in range(3):
        client.patch(f'/api/tasks/{task["id"]}', json={'done': True})
    assert len(_acts(lead['id'], 'call')) == 1


# ── Stage history is keyed, not string-matched ───────────────────────────────

def test_appointments_are_counted_by_stage_key_not_label(client):
    """The leaderboard used `body LIKE '%-> Appt Set%'`. Renaming a stage's
    label is a cosmetic edit with nothing to warn you, and it silently zeroed
    every rep's appointment count. Nothing may read the label to get a count."""
    signup(client)
    lead = new_lead(client)
    client.patch(f'/api/leads/{lead["id"]}/stage', json={'stage': 'appt_set'})

    assert client.get('/api/leaderboard').get_json()[0]['appts_set'] == 1

    # Rename the label the way a manager might, and the number must hold.
    meta = appmod.STAGE_META['appt_set']
    original = meta['label']
    meta['label'] = 'Inspection Booked'
    try:
        assert client.get('/api/leaderboard').get_json()[0]['appts_set'] == 1
    finally:
        meta['label'] = original


def test_estimates_presented_is_counted_by_stage_key(client):
    signup(client)
    lead = new_lead(client)
    client.patch(f'/api/leads/{lead["id"]}/stage', json={'stage': 'estimate_presented'})
    sc = client.get('/api/scorecard/luke').get_json()
    assert sc['estimates_presented'] == 1


def test_stage_changes_record_the_destination_key(client):
    signup(client)
    lead = new_lead(client)
    client.patch(f'/api/leads/{lead["id"]}/stage', json={'stage': 'contacted'})
    row = _acts(lead['id'], 'stage_change')[0]
    assert row['outcome'] == 'contacted'
    assert '→' in row['body'], 'the human sentence is still there for the timeline'


def test_backfill_recovers_the_key_from_an_old_log_line(client):
    """Live volumes hold years of stage_change rows written before `outcome`
    carried the key -- the only record of when each appointment was set. They
    are parsed and kept, not dropped."""
    signup(client)
    lead = new_lead(client)
    client.patch(f'/api/leads/{lead["id"]}/stage', json={'stage': 'appt_set'})
    with appmod.get_db() as db:
        db.execute("UPDATE activities SET outcome='' WHERE kind='stage_change'")
        appmod._backfill_stage_keys(db)
        row = db.execute("SELECT outcome FROM activities WHERE kind='stage_change'").fetchone()
    assert row['outcome'] == 'appt_set'


def test_backfill_reads_past_the_reason_suffix(client):
    """_auto_advance writes 'Contacted → Won (contract signed)'."""
    signup(client)
    lead = new_lead(client)
    with appmod.get_db() as db:
        appmod._log_activity(db, lead['id'], 'stage_change', rep='luke',
                             body='Contacted → Won (contract signed)')
        appmod._backfill_stage_keys(db)
        row = db.execute("SELECT outcome FROM activities WHERE kind='stage_change'").fetchone()
    assert row['outcome'] == 'won'


# ── A lead's follow-ups belong to whoever owns it now ────────────────────────

def test_reassigning_a_lead_moves_its_open_tasks(client):
    """tasks.rep is a separate column, and nothing kept it in step: a handed-over
    deal left every follow-up on the old rep's My Day and gave the new owner a
    lead with no next action."""
    signup(client, 'luke')                       # first user is the manager
    signup(client, 'casey')
    login(client, 'luke')
    lead = new_lead(client)
    client.post(f'/api/leads/{lead["id"]}/tasks', json={'kind': 'call', 'title': 'Ring them'})
    before = client.get('/api/tasks').get_json()
    assert len(before) > 1, 'the cadence schedules follow-ups too; those move as well'

    client.put(f'/api/leads/{lead["id"]}', json={'rep': 'casey'})

    login(client, 'casey')
    moved = client.get('/api/tasks').get_json()
    assert {t['id'] for t in moved} == {t['id'] for t in before}
    login(client, 'luke')
    assert client.get('/api/tasks?rep=luke').get_json() == []


def test_reassignment_is_on_the_timeline(client):
    signup(client, 'luke')
    signup(client, 'casey')
    login(client, 'luke')
    lead = new_lead(client)
    client.put(f'/api/leads/{lead["id"]}', json={'rep': 'casey'})
    bodies = [a['body'] for a in _acts(lead['id'], 'system')]
    assert any('Reassigned' in b for b in bodies)


def test_a_completed_task_keeps_the_rep_who_did_it(client):
    """Done tasks are a record of who did the work, not of who owns the lead."""
    signup(client, 'luke')
    signup(client, 'casey')
    login(client, 'luke')
    lead = new_lead(client)
    client.post(f'/api/leads/{lead["id"]}/tasks', json={'kind': 'call', 'title': 'Ring them'})
    task = _task(client, 'Ring them')
    client.patch(f'/api/tasks/{task["id"]}', json={'done': True})

    client.put(f'/api/leads/{lead["id"]}', json={'rep': 'casey'})
    with appmod.get_db() as db:
        assert db.execute('SELECT rep FROM tasks WHERE id=?',
                          (task['id'],)).fetchone()['rep'] == 'luke'


# ── The dashboard's window means the same thing everywhere on the screen ─────

def test_source_attribution_respects_the_date_window(client):
    """by_source ignored the filter entirely: switching to 'Last 7 days' left an
    all-time chart sitting beside 7-day KPIs, on the screen someone reads to
    decide where the marketing money goes."""
    signup(client)
    old = new_lead(client, source='door_knock')
    new_lead(client, source='referral')
    with appmod.get_db() as db:                 # age one lead out of the window
        db.execute("UPDATE leads SET created_at='2020-01-01T00:00:00Z' WHERE id=?",
                   (old['id'],))

    dash = client.get('/api/dashboard?days=7').get_json()
    assert dash['by_source'] == {'referral': 1}
    assert dash['by_state'] == {'??': 1}
