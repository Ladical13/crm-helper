"""Transactional, replay-safe handoff from a saved canvasser pin."""
import json
import sqlite3
import uuid
from datetime import datetime, timezone
from zoneinfo import ZoneInfo


def initialize(path):
    from portal.migration_backup import before_upgrade
    before_upgrade(path, 'canvass-handoff-v1')
    with sqlite3.connect(path) as db:
        db.execute('''CREATE TABLE IF NOT EXISTS canvass_links (
            pin_id TEXT PRIMARY KEY, lead_id TEXT NOT NULL,
            task_id TEXT NOT NULL DEFAULT '', snapshot TEXT NOT NULL DEFAULT '{}',
            lat REAL, lng REAL)''')
        db.execute('CREATE INDEX IF NOT EXISTS canvass_lead_idx ON canvass_links(lead_id)')


def sync_pin(crm, pin, stage):
    """Lead, appointment, notes and source coordinates commit together or not at all."""
    parts = pin['contact_name'].strip().split(maxsplit=1)
    fields = dict(first_name=parts[0], last_name=parts[1] if len(parts) > 1 else '',
                  phone=pin.get('contact_phone', ''), email=pin.get('contact_email', ''),
                  address=pin.get('address', ''))
    due = ''
    if pin.get('appointment_at') and pin['pin_type'] == 'appointment':
        local = datetime.fromisoformat(pin['appointment_at'])
        due = local.replace(tzinfo=ZoneInfo(pin.get('appointment_tz') or 'America/Denver')).astimezone(
            timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
    now = crm._now()
    with crm.get_db() as db:
        db.execute('BEGIN IMMEDIATE')
        link = db.execute('SELECT * FROM canvass_links WHERE pin_id=?', (pin['id'],)).fetchone()
        old = json.loads(link['snapshot']) if link else {}
        lid = link['lead_id'] if link else (pin.get('crm_lead_id') or
                str(uuid.uuid5(uuid.NAMESPACE_URL, 'p1-canvass:' + pin['id'])))
        lead = db.execute('SELECT * FROM leads WHERE id=?', (lid,)).fetchone()
        if lead and lead['rep'] != pin['rep']:
            raise ValueError('The linked lead belongs to a different rep; ask a manager to reconcile it.')
        if not lead:
            if link or pin.get('crm_lead_id'):
                raise ValueError('The linked lead was deleted; ask a manager to reconcile this door.')
            initial = dict(fields, id=lid, rep=pin['rep'], source='door_knock',
                           lead_type='homeowner', stage=stage, created_at=now, updated_at=now,
                           phone_norm=crm._norm_phone(fields['phone']),
                           email_norm=crm._norm_email(fields['email']))
            db.execute(f'INSERT INTO leads ({",".join(initial)}) VALUES ({",".join("?" for _ in initial)})',
                       list(initial.values()))
            crm._log_activity(db, lid, 'system', rep=pin['rep'], body='Lead created from a saved canvasser door.')
            cadence = crm._cadence_for(stage, 'homeowner')
            if cadence:
                crm._enroll(db, lid, pin['rep'], cadence)
        else:
            # Do not undo edits made in CRM unless the source field changed.
            changed = {k: v for k, v in fields.items()
                       if (old and v != old.get('fields', {}).get(k)) or (not old and not lead[k])}
            if 'phone' in changed:
                changed['phone_norm'] = crm._norm_phone(changed['phone'])
            if 'email' in changed:
                changed['email_norm'] = crm._norm_email(changed['email'])
            if changed:
                changed['updated_at'] = now
                db.execute(f'UPDATE leads SET {",".join(k + "=?" for k in changed)} WHERE id=?',
                           [*changed.values(), lid])
            crm._auto_advance(db, lid, stage, 'Canvasser update')
        notes = pin.get('notes', '')
        if notes and notes != old.get('notes'):
            crm._log_activity(db, lid, 'note', rep=pin['rep'], body='At the door: ' + notes)
        task_id = link['task_id'] if link else ''
        if not task_id and pin.get('crm_lead_id'):
            task = db.execute("SELECT id FROM tasks WHERE lead_id=? AND kind='meeting' "
                              "AND title LIKE 'Appointment%' AND done=0 ORDER BY created_at LIMIT 1", (lid,)).fetchone()
            task_id = task['id'] if task else ''
        if due and (due != old.get('due') or not task_id):
            task_id = task_id or str(uuid.uuid5(uuid.NAMESPACE_URL, 'p1-canvass-appointment:' + pin['id']))
            db.execute('''INSERT INTO tasks (id,lead_id,rep,kind,title,due_at,created_at)
                VALUES (?,?,?,'meeting',?,?,?) ON CONFLICT(id) DO UPDATE SET
                title=excluded.title,due_at=excluded.due_at,done=0,done_at='' ''',
                (task_id, lid, pin['rep'], 'Appointment — ' + pin.get('address', ''), due, now))
            crm._log_activity(db, lid, 'note', rep=pin['rep'], body='Door appointment scheduled for ' +
                              pin['appointment_at'] + ' ' + (pin.get('appointment_tz') or 'America/Denver'))
        elif not due and task_id:
            db.execute('UPDATE tasks SET done=1,done_at=? WHERE id=? AND done=0', (now, task_id))
        snapshot = json.dumps(dict(fields=fields, notes=notes, due=due))
        db.execute('''INSERT INTO canvass_links VALUES (?,?,?,?,?,?) ON CONFLICT(pin_id)
            DO UPDATE SET task_id=excluded.task_id,snapshot=excluded.snapshot,
            lat=excluded.lat,lng=excluded.lng''',
            (pin['id'], lid, task_id, snapshot, pin['lat'], pin['lng']))
        crm._refresh_contact_quality(db, lid)
        crm._refresh_next_action(db, lid)
    return {'id': lid, 'appointment_synced': bool(due)}
