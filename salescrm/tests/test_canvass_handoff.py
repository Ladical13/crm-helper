import copy
import pytest
from flask import session
from conftest import signup
from salescrm.canvass import sync_pin


def pin(**updates):
    value = dict(id='door-one', rep='luke', pin_type='appointment',
                 contact_name='Ada Homeowner', contact_phone='9705551212', contact_email='',
                 address='123 Audit Way, Fort Collins CO', notes='Call first',
                 lat=40.585, lng=-105.085, appointment_at='2026-09-25T18:00',
                 appointment_tz='America/Denver', crm_lead_id='')
    value.update(updates)
    return value


def run(app, value):
    with app.app.test_request_context():
        session['username'] = 'luke'
        return sync_pin(app, value, 'appt_set')


def test_replay_after_lost_pin_link_has_one_lead_and_one_meeting(client, app):
    signup(client)
    first = run(app, pin())
    second = run(app, pin())
    assert first == second
    with app.get_db() as db:
        assert db.execute('SELECT COUNT(*) FROM leads').fetchone()[0] == 1
        meetings = db.execute("SELECT * FROM tasks WHERE kind='meeting'").fetchall()
        assert len(meetings) == 1
        assert meetings[0]['due_at'] == '2026-09-26T00:00:00Z'
    assert app._lead_point({'id': first['id'], 'address': ''}) == (40.585, -105.085)


def test_task_failure_rolls_back_entire_handoff_then_retry_succeeds(client, app, monkeypatch):
    signup(client)
    original = app._log_activity
    def fail(db, lead_id, kind, **kw):
        if kw.get('body', '').startswith('Door appointment'):
            raise RuntimeError('simulated task transaction interruption')
        return original(db, lead_id, kind, **kw)
    monkeypatch.setattr(app, '_log_activity', fail)
    with pytest.raises(RuntimeError):
        run(app, pin())
    with app.get_db() as db:
        for table in ('leads', 'tasks', 'canvass_links'):
            assert db.execute(f'SELECT COUNT(*) FROM {table}').fetchone()[0] == 0
    monkeypatch.setattr(app, '_log_activity', original)
    assert run(app, pin())['appointment_synced']


def test_reschedule_updates_existing_task_and_cancel_closes_it(client, app):
    signup(client)
    first = run(app, pin())
    changed = pin(appointment_at='2026-09-26T09:00', crm_lead_id=first['id'])
    run(app, changed)
    with app.get_db() as db:
        tasks = db.execute("SELECT * FROM tasks WHERE kind='meeting'").fetchall()
        assert len(tasks) == 1 and tasks[0]['due_at'] == '2026-09-26T15:00:00Z'
    run(app, dict(changed, pin_type='interested', appointment_at=''))
    with app.get_db() as db:
        assert db.execute("SELECT done FROM tasks WHERE kind='meeting'").fetchone()[0] == 1


def test_replay_does_not_overwrite_crm_edits_or_regress_signed_lead(client, app):
    signup(client)
    first = run(app, pin())
    with app.get_db() as db:
        db.execute("UPDATE leads SET phone='new CRM phone', stage='won' WHERE id=?", (first['id'],))
    run(app, pin())
    with app.get_db() as db:
        row = db.execute('SELECT phone,stage FROM leads').fetchone()
        assert row['phone'] == 'new CRM phone' and row['stage'] == 'won'


def test_legacy_link_adopts_existing_lead_and_meeting(client, app):
    signup(client)
    first = run(app, pin())
    with app.get_db() as db:
        db.execute('DELETE FROM canvass_links')
    assert run(app, pin(crm_lead_id=first['id']))['id'] == first['id']
    with app.get_db() as db:
        assert db.execute('SELECT COUNT(*) FROM leads').fetchone()[0] == 1
        assert db.execute("SELECT COUNT(*) FROM tasks WHERE kind='meeting'").fetchone()[0] == 1
