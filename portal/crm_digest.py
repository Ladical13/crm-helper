"""The morning follow-up email: each rep's due work, in their inbox at 7am.

Follow-ups only get done if the rep opens the CRM, and the one they promised
("call me back Tuesday") is the one that costs the most when it is missed.
This mails each rep a short list every morning: callbacks they promised,
overdue follow-ups, what is due today, and interested leads still waiting on an
appointment. A rep with nothing due gets nothing.

Read-only against salescrm.db (opened `mode=ro`), same as portal/backup.py —
the digest must never be the thing that locks a rep's save. Sent from the
estimator's hourly loop, which owns the configured mail sender and the
lockfile convention that stops two gunicorn workers sending it twice.
Turn it off with SALESCRM_DIGEST=0.
"""
import html
import os
import sqlite3
from datetime import datetime, time, timedelta, timezone

from . import users as pusers

COMPANY_TZ = 'America/Denver'
SEND_AFTER_HOUR = 7          # local time
SKIP_WEEKDAYS = (6,)         # Sunday
MAX_ROWS = 15
CALLBACK_TITLE = 'Call back - they asked for this time'   # salescrm OUTCOMES['callback']


def enabled():
    return os.environ.get('SALESCRM_DIGEST', '1').strip() not in ('0', 'false', 'no')


def _tz():
    try:
        from zoneinfo import ZoneInfo
        return ZoneInfo(COMPANY_TZ)
    except Exception:
        return timezone.utc


def local_now():
    return datetime.now(timezone.utc).astimezone(_tz())


def due_now(now=None):
    """True once it is past 7am in Colorado, and not a Sunday."""
    now = now or local_now()
    return now.hour >= SEND_AFTER_HOUR and now.weekday() not in SKIP_WEEKDAYS


def db_path():
    d = (os.environ.get('SALESCRM_DATA_DIR') or os.environ.get('DATA_DIR')
         or os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'salescrm'))
    return os.path.join(d, 'salescrm.db')


def _iso(dt):
    return dt.astimezone(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')


def build(path=None, now=None):
    """{rep: {'callbacks': [...], 'overdue': [...], 'today': [...], 'interested': n}}."""
    path = path or db_path()
    if not os.path.exists(path):
        return {}
    now = now or local_now()
    start = datetime.combine(now.date(), time.min, tzinfo=now.tzinfo)
    end = datetime.combine(now.date(), time.max, tzinfo=now.tzinfo)
    db = sqlite3.connect(f'file:{path}?mode=ro', uri=True)
    db.row_factory = sqlite3.Row
    try:
        rows = db.execute(
            "SELECT t.rep, t.title, t.kind, t.due_at, l.first_name, l.last_name, l.company, "
            "       l.phone, l.city "
            "FROM tasks t JOIN leads l ON l.id = t.lead_id "
            "WHERE t.done = 0 AND t.due_at <= ? AND l.dnc = 0 "
            "ORDER BY t.due_at", (_iso(end),)).fetchall()
        cols = [r[1] for r in db.execute('PRAGMA table_info(leads)')]
        interested = {}
        if 'outreach_status' in cols:
            interested = {r['rep']: r['n'] for r in db.execute(
                "SELECT rep, COUNT(*) n FROM leads WHERE outreach_status='interested' "
                "AND stage NOT IN ('won','lost') GROUP BY rep")}
    finally:
        db.close()
    out = {}
    for r in rows:
        name = (f"{r['first_name']} {r['last_name']}".strip() or r['company'] or '(no name)')
        item = {'name': name, 'title': r['title'] or r['kind'], 'phone': r['phone'],
                'city': r['city'], 'due_at': r['due_at']}
        rep = out.setdefault(r['rep'], {'callbacks': [], 'overdue': [], 'today': [],
                                        'interested': 0})
        if r['title'] == CALLBACK_TITLE:
            rep['callbacks'].append(item)
        elif r['due_at'] < _iso(start):
            rep['overdue'].append(item)
        else:
            rep['today'].append(item)
    for rep, n in interested.items():
        out.setdefault(rep, {'callbacks': [], 'overdue': [], 'today': [], 'interested': 0})
        out[rep]['interested'] = n
    return {rep: d for rep, d in out.items()
            if d['callbacks'] or d['overdue'] or d['today'] or d['interested']}


def render(rep, d, base_url=''):
    """(subject, html) for one rep."""
    n = len(d['callbacks']) + len(d['overdue']) + len(d['today'])
    bits = []
    if d['callbacks']:
        bits.append(f"{len(d['callbacks'])} callback{'s' if len(d['callbacks']) != 1 else ''} you promised")
    if d['overdue']:
        bits.append(f"{len(d['overdue'])} overdue")
    subject = f"Today: {n} follow-up{'s' if n != 1 else ''}" + (f" ({', '.join(bits)})" if bits else '')
    if not n and d['interested']:
        subject = f"{d['interested']} interested lead{'s' if d['interested'] != 1 else ''} waiting on an appointment"

    def section(title, items):
        if not items:
            return ''
        trs = ''.join(
            f"<tr><td style='padding:4px 10px 4px 0'><b>{html.escape(i['name'])}</b>"
            f"{' &middot; ' + html.escape(i['city']) if i['city'] else ''}</td>"
            f"<td style='padding:4px 10px 4px 0;color:#555'>{html.escape(i['title'])}</td>"
            f"<td style='padding:4px 0'>{html.escape(i['phone'] or '')}</td></tr>"
            for i in items[:MAX_ROWS])
        more = (f"<p style='color:#777;margin:4px 0'>+ {len(items) - MAX_ROWS} more in the CRM</p>"
                if len(items) > MAX_ROWS else '')
        return (f"<h3 style='margin:16px 0 6px;font-size:15px'>{title} ({len(items)})</h3>"
                f"<table style='border-collapse:collapse;font-size:14px'>{trs}</table>{more}")

    link = (base_url.rstrip('/') + '/crm/') if base_url else '/crm/'
    first = pusers.display_name(rep).split(' ')[0] if pusers.display_name(rep) else rep
    body = (
        f"<div style='font-family:-apple-system,Segoe UI,Arial,sans-serif;color:#111'>"
        f"<p>Morning {html.escape(first)},</p>"
        + section('📅 Callbacks you promised', d['callbacks'])
        + section('⏰ Overdue', d['overdue'])
        + section('Due today', d['today'])
        + (f"<p style='margin-top:16px'>👍 <b>{d['interested']}</b> interested "
           f"lead{'s' if d['interested'] != 1 else ''} still need an appointment booked.</p>"
           if d['interested'] else '')
        + f"<p style='margin-top:18px'><a href='{html.escape(link)}' "
          f"style='background:#f97316;color:#fff;padding:10px 16px;border-radius:8px;"
          f"text-decoration:none;font-weight:700'>Open the Outreach tab</a></p>"
        + "<p style='color:#999;font-size:12px'>Sent every morning you have follow-ups due.</p></div>")
    return subject, body


def send_all(send_email, base_url='', path=None, now=None):
    """Mail every rep with work due. Returns how many were sent."""
    sent = 0
    for rep, d in build(path, now).items():
        if not pusers.get(rep):                  # account removed; nobody to tell
            continue
        subject, body = render(rep, d, base_url)
        try:
            send_email(subject, body, pusers.email_of(rep))
            sent += 1
        except Exception as e:                   # one bad address must not stop the rest
            print(f'[crm-digest] send to {rep} failed: {e}')
    return sent
