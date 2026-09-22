"""Tell somebody when hail lands on the service area.

The archive going in nightly is half of it. The other half is that nobody reads
a database: a storm at 2am is worth knowing about at 6am, not whenever someone
next opens the map and thinks to check last week.

**It alerts on the SERVICE AREA, not on our leads.** The CRM already joins a
storm to the leads under it (`/api/storms`) and books follow-ups from that, and
that is a different job — working the book we have. This is the other question,
and for a roofer it is the bigger one: did hail fall on ground we could be
knocking. A street with no lead on it is not less interesting, it is *more*,
because nobody has been there yet.

`send_email` is INJECTED rather than imported, the same as `portal/backup.py`
and `portal/crm_digest.py`. The sender lives in the estimator with the SMTP and
SendGrid config; importing it here would drag a 24,000-line module into the
storm archive to send one message.

**An alert is sent once per storm day and never again.** `storm_alerts` records
what went out, so a re-ingest — which is routine, since the most recent days are
re-fetched while their rolling maximum settles — does not mail the same storm
every night until somebody turns the whole thing off.
"""
import os

from . import grid as hgrid
from . import storms

# Northern Colorado: the ground the company actually works. Deliberately a box
# rather than a radius — a radius around an office says nothing about a service
# area that runs up the I-25 corridor. `HAIL_ALERT_BOUNDS` overrides it as
# "south,west,north,east" for when the area changes or a second market opens.
SERVICE_AREA = (39.90, -105.60, 40.90, -104.40)

# What is worth waking up for. Below an inch a roof is rarely totalled and an
# alert nobody acts on is an alert nobody reads.
ALERT_MIN_IN = 1.0

# How far back a run will look for storms it has not alerted on. Long enough to
# cover a weekend of failed sends, short enough that first light on a brand new
# archive does not mail three years of history in one message.
ALERT_WINDOW_DAYS = 3


def bounds():
    raw = (os.environ.get('HAIL_ALERT_BOUNDS') or '').strip()
    if not raw:
        return SERVICE_AREA
    try:
        s, w, n, e = (float(x) for x in raw.split(','))
    except (ValueError, TypeError):
        # A malformed variable must not silence the alerts. Falling back is the
        # safe direction: the worst case is an alert about the default area.
        return SERVICE_AREA
    return (min(s, n), min(w, e), max(s, n), max(w, e))


def _init():
    with storms.get_db() as db:
        db.execute('''CREATE TABLE IF NOT EXISTS storm_alerts (
                          event_id TEXT PRIMARY KEY,
                          sent_at  TEXT NOT NULL,
                          cells    INTEGER NOT NULL DEFAULT 0,
                          max_size REAL    NOT NULL DEFAULT 0
                      )''')
        db.commit()


def already_sent():
    _init()
    with storms.get_db() as db:
        return {r['event_id'] for r in db.execute('SELECT event_id FROM storm_alerts')}


def mark_sent(rows):
    _init()
    with storms.get_db() as db:
        for r in rows:
            db.execute('INSERT OR REPLACE INTO storm_alerts '
                       '(event_id, sent_at, cells, max_size) VALUES (?,?,?,?)',
                       (r['event_id'], storms._now(), r['cells'], r['max_size']))
        db.commit()


def pending(area=None, min_size=ALERT_MIN_IN, days=ALERT_WINDOW_DAYS, today=None):
    """Storm days over the service area that have not been alerted on.

    Newest first. Each row carries the cells that actually fell inside the box,
    so the alert can say where — a statewide maximum is useless to a rep who
    works one county.
    """
    from datetime import timedelta

    from portal import clock

    area = area or bounds()
    today = today or clock.company_today()
    since = (today - timedelta(days=int(days))).isoformat()
    sent = already_sent()
    out = []
    # BOTH ends. `storm_days(since=...)` has no upper bound of its own, so a
    # floor alone is not a window — it is everything from that date onward,
    # which is the whole archive once one is loaded.
    for day in storms.storm_days(since=since, until=today.isoformat(),
                                 min_size=min_size, limit=200):
        eid = day['event_id']
        if eid in sent:
            continue
        rows, _ = storms.cells_in(bounds=area, since=day['event_date'],
                                  until=day['event_date'], min_size=min_size,
                                  limit=100000)
        if not rows:
            continue                       # hit Colorado, missed us
        out.append({
            'event_id':   eid,
            'event_date': day['event_date'],
            'cells':      len(rows),
            'max_size':   round(max(r[4] for r in rows), 2),
            'rects':      rows,
        })
    return out


# Where a rep would actually go. A storm brief that says "1.8 inches somewhere
# in a 70-mile box" cannot be acted on; "Severance and Windsor" can. These are
# the towns the company works, and the nearest one to a cell is how a swath
# turns into a place. Not a geocoder: this runs from a background thread on a
# schedule and must not depend on a third party being up.
TOWNS = {
    'Fort Collins': (40.5853, -105.0844), 'Loveland':    (40.3978, -105.0750),
    'Greeley':      (40.4233, -104.7091), 'Windsor':     (40.4775, -104.9014),
    'Longmont':     (40.1672, -105.1019), 'Wellington':  (40.7047, -105.0083),
    'Johnstown':    (40.3369, -104.9122), 'Berthoud':    (40.3083, -105.0811),
    'Eaton':        (40.5297, -104.7122), 'Severance':   (40.5250, -104.8511),
    'Timnath':      (40.5297, -104.9855), 'Evans':       (40.3763, -104.6919),
    'Milliken':     (40.3283, -104.8553), 'Ault':        (40.5847, -104.7333),
    'Platteville':  (40.2169, -104.8217), 'Fort Lupton': (40.0811, -104.8125),
    'Erie':         (40.0503, -105.0500), 'Firestone':   (40.1119, -104.9367),
    'Brighton':     (39.9853, -104.8206), 'Mead':        (40.2336, -104.9958),
}

# Past this a cell is not "near" a town in any sense a rep would accept; it is
# open county and the brief says so rather than naming somewhere ten miles off.
NEAR_MILES = 10.0


def _miles(lat1, lng1, lat2, lng2):
    import math
    return 69.0 * math.hypot(lat1 - lat2,
                             (lng1 - lng2) * math.cos(math.radians(lat1)))


def places(rects, near_miles=NEAR_MILES):
    """[(town, cells, max_size)] worst first, plus an 'open county' bucket.

    A cell belongs to its NEAREST town, so a swath between two of them is not
    double-counted into both and made to look twice its size.
    """
    hit = {}
    for s, w, n, e, size in rects:
        la, ln = (s + n) / 2.0, (w + e) / 2.0
        best, best_d = None, None
        for town, (tla, tln) in TOWNS.items():
            d = _miles(la, ln, tla, tln)
            if best_d is None or d < best_d:
                best, best_d = town, d
        # Naming the nearest town anyway, with the distance, turns a row a rep
        # cannot act on into a bearing they can. "open county" alone is true
        # and useless.
        key = (best if best_d is not None and best_d <= near_miles
               else (f'open county (nearest {best}, {best_d:.0f} mi)'
                     if best is not None else 'open county'))
        cur = hit.setdefault(key, {'cells': 0, 'max_size': 0.0})
        cur['cells'] += 1
        cur['max_size'] = max(cur['max_size'], size)
    return sorted(((k, v['cells'], round(v['max_size'], 2)) for k, v in hit.items()),
                  key=lambda r: (-r[2], -r[1]))


def _esc(s):
    return (str(s).replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;'))


def brief_html(events, base_url=''):
    """The email. Every number is the archive's own; nothing is extrapolated."""
    parts = ['<div style="font-family:system-ui,-apple-system,Segoe UI,sans-serif;'
             'max-width:640px">']
    for ev in events:
        rows = places(ev['rects'])
        parts.append(
            f'<h2 style="margin:18px 0 4px">{_esc(ev["event_date"])} &mdash; '
            f'up to {ev["max_size"]:.2f}&quot; hail</h2>'
            f'<div style="color:#555;font-size:13px;margin-bottom:8px">'
            f'{ev["cells"]} radar cell(s) over the service area. Each cell is '
            f'about one square kilometre of ground the radar made a claim '
            f'about &mdash; not a spotter call-in.</div>'
            '<table cellpadding="6" style="border-collapse:collapse;font-size:14px">')
        for town, cells, mx in rows:
            parts.append(
                f'<tr><td style="border-bottom:1px solid #eee"><b>{_esc(town)}</b></td>'
                f'<td style="border-bottom:1px solid #eee">{mx:.2f}&quot;</td>'
                f'<td style="border-bottom:1px solid #eee;color:#666">{cells} cell(s)</td></tr>')
        parts.append('</table>')
        if base_url:
            parts.append(
                f'<p style="margin:10px 0 0"><a href="{_esc(base_url)}/canvass/">'
                f'Open the canvasser and draw {_esc(ev["event_date"])} on the map</a></p>')
    parts.append('<hr style="margin:22px 0;border:none;border-top:1px solid #eee">'
                 '<div style="color:#888;font-size:12px">MRMS MESH is a radar '
                 '<i>estimate</i> of the largest hail a storm could produce, not a '
                 'measurement of what landed. It is the right tool for deciding '
                 'where to knock; it is not a number to quote a homeowner as fact.'
                 '</div></div>')
    return ''.join(parts)


def send_new(send_email, to_addr, base_url='', area=None, min_size=ALERT_MIN_IN,
             days=ALERT_WINDOW_DAYS, today=None):
    """Mail a brief for each un-alerted storm over the service area.

    Returns a summary. Marks sent only when the send REPORTS success, so a
    bounced or unconfigured mailer leaves the storm pending rather than
    swallowing it — the one failure mode that would be invisible.
    """
    if not to_addr:
        return {'sent': 0, 'events': [], 'note': 'no recipient configured'}
    events = pending(area=area, min_size=min_size, days=days, today=today)
    if not events:
        return {'sent': 0, 'events': [], 'note': 'no new storms over the service area'}

    worst = max(e['max_size'] for e in events)
    when = events[0]['event_date'] if len(events) == 1 else \
        f'{events[-1]["event_date"]} to {events[0]["event_date"]}'
    subject = f'🌩 Hail in the service area — {when}, up to {worst:.2f}"'
    if not send_email(subject, brief_html(events, base_url), to_addr):
        return {'sent': 0, 'events': [e['event_id'] for e in events],
                'note': 'send failed — left pending for the next run'}
    mark_sent(events)
    return {'sent': len(events), 'events': [e['event_id'] for e in events],
            'max_size': worst}
