"""The morning follow-up email.

What matters: a promised callback is called out on its own, a rep with nothing
due gets nothing, an opted-out lead never appears, and it goes out after 7am
Colorado time — never at 1am because the server runs in UTC.
"""
import os
import sqlite3
from datetime import datetime, timedelta, timezone

import pytest

from portal import crm_digest
from portal import users as pusers


def _db(tmp_path, tasks, leads_extra=None):
    p = str(tmp_path / 'salescrm.db')
    db = sqlite3.connect(p)
    db.executescript('''
        CREATE TABLE leads (id TEXT, first_name TEXT, last_name TEXT, company TEXT,
            phone TEXT, city TEXT, dnc INTEGER DEFAULT 0, rep TEXT, stage TEXT DEFAULT 'new',
            outreach_status TEXT DEFAULT 'not_contacted');
        CREATE TABLE tasks (id TEXT, lead_id TEXT, rep TEXT, kind TEXT, title TEXT,
            due_at TEXT, done INTEGER DEFAULT 0);
    ''')
    for i, (rep, title, due, extra) in enumerate(tasks):
        lead = dict(id=f'l{i}', first_name=f'Pat{i}', last_name='', company='', phone='970',
                    city='Loveland', dnc=0, rep=rep, stage='new', outreach_status='attempted')
        lead.update(extra or {})
        db.execute(f'INSERT INTO leads ({",".join(lead)}) VALUES ({",".join("?" * len(lead))})',
                   list(lead.values()))
        db.execute('INSERT INTO tasks VALUES (?,?,?,?,?,?,0)',
                   (f't{i}', f'l{i}', rep, 'call', title, due))
    for lead in leads_extra or []:
        db.execute(f'INSERT INTO leads ({",".join(lead)}) VALUES ({",".join("?" * len(lead))})',
                   list(lead.values()))
    db.commit()
    db.close()
    return p


def _now(hour=8):
    return datetime(2026, 9, 21, hour, 0, tzinfo=crm_digest._tz())      # a Monday


def _utc(dt):
    return dt.astimezone(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')


def test_callbacks_overdue_and_today_are_separated(tmp_path):
    now = _now()
    p = _db(tmp_path, [
        ('casey', crm_digest.CALLBACK_TITLE, _utc(now + timedelta(hours=2)), None),
        ('casey', 'Try again', _utc(now - timedelta(days=2)), None),
        ('casey', 'Text after the voicemail', _utc(now + timedelta(hours=5)), None),
        ('casey', 'Next week', _utc(now + timedelta(days=5)), None),
    ])
    d = crm_digest.build(p, now)['casey']
    assert [len(d[k]) for k in ('callbacks', 'overdue', 'today')] == [1, 1, 1]


def test_a_rep_with_nothing_due_gets_nothing(tmp_path):
    now = _now()
    p = _db(tmp_path, [('casey', 'Next week', _utc(now + timedelta(days=5)), None)])
    assert crm_digest.build(p, now) == {}


def test_an_opted_out_lead_never_appears(tmp_path):
    now = _now()
    p = _db(tmp_path, [('casey', 'Try again', _utc(now), {'dnc': 1})])
    assert crm_digest.build(p, now) == {}


def test_interested_leads_are_counted_even_without_a_task(tmp_path):
    now = _now()
    p = _db(tmp_path, [], leads_extra=[dict(id='x', first_name='A', last_name='', company='',
            phone='', city='', dnc=0, rep='casey', stage='contacted', outreach_status='interested')])
    assert crm_digest.build(p, now)['casey']['interested'] == 1


@pytest.mark.parametrize('hour,weekday_offset,expect', [
    (6, 0, False), (7, 0, True), (15, 0, True), (9, 6, False)])       # +6 days = Sunday
def test_it_goes_out_after_7am_colorado_and_never_sunday(hour, weekday_offset, expect):
    assert crm_digest.due_now(_now(hour) + timedelta(days=weekday_offset)) is expect


def test_the_subject_leads_with_the_promised_callbacks(tmp_path):
    now = _now()
    p = _db(tmp_path, [('casey', crm_digest.CALLBACK_TITLE, _utc(now), None),
                       ('casey', 'Try again', _utc(now), None)])
    subject, body = crm_digest.render('casey', crm_digest.build(p, now)['casey'], 'https://x')
    assert subject == 'Today: 2 follow-ups (1 callback you promised)'
    assert 'https://x/crm/' in body


def test_send_all_mails_each_rep_at_their_address(tmp_path):
    for u in ('casey', 'jacob'):
        if not pusers.get(u):
            pusers.create(u, password='test-only-password', role='rep', full_name=u.title())
    now = _now()
    p = _db(tmp_path, [('casey', 'Try again', _utc(now), None),
                       ('jacob', 'Try again', _utc(now), None),
                       ('ghost', 'Try again', _utc(now), None)])      # no account: skipped
    sent = []
    n = crm_digest.send_all(lambda s, b, to: sent.append(to), 'https://x', p, now)
    assert n == 2
    assert sorted(sent) == sorted([pusers.email_of('casey'), pusers.email_of('jacob')])


def test_it_can_be_switched_off(monkeypatch):
    monkeypatch.setenv('SALESCRM_DIGEST', '0')
    assert crm_digest.enabled() is False
