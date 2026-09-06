"""An appointment has a time, and the time is the point.

`appt_set` was a stage with no clock. A rep booked Thursday at 2pm and the CRM
had nowhere to put it, so the day's schedule lived in the rep's head.
"""
from conftest import signup, login, new_lead
import app as appmod


def _book(client, lead_id, when):
    return client.patch(f'/api/leads/{lead_id}/stage',
                        json={'stage': 'appt_set', 'appt_at': when}).get_json()


def _at(hours):
    from datetime import timedelta
    return appmod._iso(appmod._now_dt() + timedelta(hours=hours))


def test_booking_records_the_time(client):
    signup(client)
    lead = new_lead(client)
    when = _at(4)
    assert _book(client, lead['id'], when)['appt_at'] == when


def test_the_appointment_becomes_the_next_action(client):
    """With nothing else outstanding, the appointment IS the next action.

    Created at `contacted`, which enrols no cadence -- a 7-touch lead's first
    call is due today, genuinely sooner than a 2pm appointment, and next_action
    correctly picks that instead.
    """
    signup(client)
    lead = new_lead(client, stage='contacted')
    got = _book(client, lead['id'], _at(2))
    assert got['next_action_at'] == got['appt_at']


def test_an_inspected_lead_is_not_permanently_overdue(client):
    """Once the rep has moved them on, the appointment happened. Leaving it
    driving next_action_at would flag every inspected lead overdue forever."""
    signup(client)
    lead = new_lead(client)
    _book(client, lead['id'], _at(-48))          # yesterday's appointment
    got = client.patch(f'/api/leads/{lead["id"]}/stage',
                       json={'stage': 'inspected'}).get_json()
    assert got['appt_at'], 'the appointment is kept as history'
    assert got['next_action_at'] != got['appt_at']


def test_a_booking_with_no_time_is_flagged_not_rejected(client):
    """The canvasser creates leads straight into appt_set from a doorstep, so
    rejecting an untimed booking would break the handoff the tool exists for.
    It is surfaced instead."""
    signup(client)
    lead = new_lead(client)
    got = client.patch(f'/api/leads/{lead["id"]}/stage',
                       json={'stage': 'appt_set'}).get_json()
    assert got['appt_missing'] is True
    assert client.get('/api/appointments').get_json()['missing_time'] == 1


def test_a_timed_booking_is_not_flagged(client):
    signup(client)
    lead = new_lead(client)
    assert _book(client, lead['id'], _at(3))['appt_missing'] is False


# ── The day's schedule ───────────────────────────────────────────────────────

def test_appointments_come_back_in_time_order(client):
    signup(client)
    late, early = new_lead(client), new_lead(client)
    _book(client, late['id'], _at(6))
    _book(client, early['id'], _at(2))
    got = client.get('/api/appointments').get_json()['appointments']
    assert [a['id'] for a in got] == [early['id'], late['id']]


def test_todays_schedule_excludes_tomorrow(client):
    signup(client)
    today, tomorrow = new_lead(client), new_lead(client)
    _book(client, today['id'], _at(2))
    _book(client, tomorrow['id'], _at(30))
    assert len(client.get('/api/appointments').get_json()['appointments']) == 1
    assert len(client.get('/api/appointments?days=2').get_json()['appointments']) == 2


def test_won_and_lost_appointments_drop_off_the_schedule(client):
    signup(client)
    lead = new_lead(client)
    _book(client, lead['id'], _at(2))
    client.patch(f'/api/leads/{lead["id"]}/stage', json={'stage': 'lost'})
    assert client.get('/api/appointments').get_json()['appointments'] == []


def test_a_rep_only_sees_their_own_schedule(client):
    signup(client, 'luke')                       # manager
    signup(client, 'casey')
    login(client, 'luke')
    mine = new_lead(client)
    _book(client, mine['id'], _at(2))
    login(client, 'casey')
    assert client.get('/api/appointments').get_json()['appointments'] == []
    assert client.get('/api/appointments?rep=luke').get_json()['appointments'] == []


def test_the_schedule_reaches_past_the_lead_list_cap(client):
    """The reason this is its own endpoint. /api/leads is capped, and once
    prospecting has imported partners by the thousand the cap is most of the
    table -- the appointment outside that page is the one a rep misses."""
    signup(client)
    lead = new_lead(client)
    _book(client, lead['id'], _at(2))
    for _ in range(5):
        new_lead(client)
    with appmod.get_db() as db:                   # push it off the first page
        db.execute("UPDATE leads SET updated_at='2020-01-01T00:00:00Z' WHERE id=?",
                   (lead['id'],))
    listed = client.get('/api/leads?limit=3').get_json()
    assert lead['id'] not in [x['id'] for x in listed]
    assert [a['id'] for a in
            client.get('/api/appointments').get_json()['appointments']] == [lead['id']]


# ── Moving one is an event, not an overwrite ─────────────────────────────────

def test_rescheduling_is_on_the_timeline(client):
    """'We rescheduled them twice' is the story a manager needs on Monday, and
    a bare overwritten column cannot tell it."""
    signup(client)
    lead = new_lead(client)
    _book(client, lead['id'], _at(2))
    client.put(f'/api/leads/{lead["id"]}', json={'appt_at': _at(26)})
    bodies = [a['body'] for a in
              client.get(f'/api/leads/{lead["id"]}').get_json()['activities']]
    assert any('Appointment set' in b for b in bodies)
    assert any('Appointment moved' in b for b in bodies)


def test_rescheduling_moves_the_next_action(client):
    signup(client)
    lead = new_lead(client, stage='contacted')
    _book(client, lead['id'], _at(2))
    later = _at(26)
    client.put(f'/api/leads/{lead["id"]}', json={'appt_at': later})
    assert client.get(f'/api/leads/{lead["id"]}').get_json()['next_action_at'] == later


def test_clearing_an_appointment_is_recorded(client):
    signup(client)
    lead = new_lead(client)
    _book(client, lead['id'], _at(2))
    client.put(f'/api/leads/{lead["id"]}', json={'appt_at': ''})
    bodies = [a['body'] for a in
              client.get(f'/api/leads/{lead["id"]}').get_json()['activities']]
    assert any('Appointment cleared' in b for b in bodies)


def test_a_managers_own_schedule_is_their_own(client):
    """Passing no rep used to mean "every rep", so a manager's My Day showed
    twenty other people's appointments as their morning."""
    signup(client, 'luke')                       # manager
    signup(client, 'casey')
    login(client, 'casey')
    theirs = new_lead(client)
    _book(client, theirs['id'], _at(2))

    login(client, 'luke')
    assert client.get('/api/appointments').get_json()['appointments'] == []
    named = client.get('/api/appointments?rep=casey').get_json()['appointments']
    assert [a['id'] for a in named] == [theirs['id']]


def test_rescheduling_returns_the_new_next_action_immediately(client):
    """The PUT response was built from the row read before next_action_at was
    recomputed, so the caller got a value that was already wrong."""
    signup(client)
    lead = new_lead(client, stage='contacted')
    _book(client, lead['id'], _at(2))
    later = _at(26)
    assert client.put(f'/api/leads/{lead["id"]}',
                      json={'appt_at': later}).get_json()['next_action_at'] == later
