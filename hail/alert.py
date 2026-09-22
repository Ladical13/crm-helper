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

# The whole state, matching what the ingest clips to. A storm outside the
# service area is still worth knowing about — it is where the next crew goes,
# and a 3-inch swath two counties over is a business decision rather than
# trivia — so the brief reports it, in its own section, under the part that is
# ours. `HAIL_ALERT_AREA_ONLY=1` drops it for anyone who wants the short mail.
STATEWIDE = (36.99, -109.06, 41.01, -102.04)


def statewide_on():
    return (os.environ.get('HAIL_ALERT_AREA_ONLY', '').strip()
            not in ('1', 'true', 'yes'))

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
    outer = STATEWIDE if statewide_on() else area
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
        rows, _ = storms.cells_in(bounds=outer, since=day['event_date'],
                                  until=day['event_date'], min_size=min_size,
                                  limit=200000)
        if not rows:
            continue                       # nothing anywhere we look
        # Split by the inner box rather than querying twice: one pass over the
        # cells we already have, and the two halves cannot disagree about which
        # side a cell fell on.
        s0, w0, n0, e0 = area
        ours = [r for r in rows
                if s0 <= (r[0] + r[2]) / 2 <= n0 and w0 <= (r[1] + r[3]) / 2 <= e0]
        elsewhere = [r for r in rows if r not in ours]
        out.append({
            'event_id':   eid,
            'event_date': day['event_date'],
            'cells':      len(ours),
            'max_size':   round(max((r[4] for r in ours), default=0), 2),
            'rects':      ours,
            'away_cells': len(elsewhere),
            'away_max':   round(max((r[4] for r in elsewhere), default=0), 2),
            'away_rects': elsewhere,
        })
    return out


# Where a rep would actually go. A storm brief that says "1.8 inches somewhere
# in a 70-mile box" cannot be acted on; "Severance and Windsor" can. The
# nearest one to a cell is how a swath turns into a place.
#
# Not a geocoder: this runs from a background thread on a schedule and must not
# depend on a third party being up. Coordinates are approximate town centres,
# used only to name a place and a rough distance — never to decide which cell
# was hit, which is `cell_index`'s job and is exact.
#
# The service-area towns come first because they are the ones a rep is sent to.
# The rest of the state is here so a statewide brief can still say WHERE, which
# is the whole reason to report it at all.
TOWNS = {
    # Northern Colorado — the service area
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
    'Estes Park':   (40.3772, -105.5217), 'Boulder':     (40.0150, -105.2705),
    # Denver metro
    'Denver':       (39.7392, -104.9903), 'Aurora':      (39.7294, -104.8319),
    'Lakewood':     (39.7047, -105.0814), 'Arvada':      (39.8028, -105.0875),
    'Westminster':  (39.8367, -105.0372), 'Thornton':    (39.8680, -104.9719),
    'Centennial':   (39.5807, -104.8772), 'Littleton':   (39.6133, -105.0166),
    'Parker':       (39.5186, -104.7614), 'Castle Rock': (39.3722, -104.8561),
    'Golden':       (39.7555, -105.2211), 'Commerce City':(39.8083, -104.9339),
    # Front Range, south
    'Colorado Springs': (38.8339, -104.8214), 'Pueblo':   (38.2544, -104.6091),
    'Monument':     (39.0917, -104.8728), 'Cañon City':  (38.4409, -105.2425),
    'Trinidad':     (37.1695, -104.5005), 'Walsenburg':  (37.6239, -104.7802),
    'Salida':       (38.5347, -105.9989),
    # Eastern plains
    'Sterling':     (40.6255, -103.2077), 'Fort Morgan': (40.2503, -103.7999),
    'Limon':        (39.2636, -103.6921), 'Burlington':  (39.3061, -102.2652),
    'Lamar':        (38.0872, -102.6202), 'La Junta':    (37.9850, -103.5438),
    'Akron':        (40.1625, -103.2147), 'Yuma':        (40.1225, -102.7252),
    'Wray':         (40.0755, -102.2235), 'Holyoke':     (40.5847, -102.2999),
    'Julesburg':    (40.9878, -102.2635), 'Springfield': (37.4083, -102.6152),
    # Western slope and mountains
    'Grand Junction': (39.0639, -108.5506), 'Montrose':  (38.4783, -107.8762),
    'Durango':      (37.2753, -107.8801), 'Cortez':      (37.3489, -108.5859),
    'Alamosa':      (37.4695, -105.8700), 'Craig':       (40.5153, -107.5464),
    'Steamboat Springs': (40.4850, -106.8317),
    'Glenwood Springs': (39.5505, -107.3248), 'Rifle':    (39.5347, -107.7831),
    'Vail':         (39.6403, -106.3742), 'Breckenridge':(39.4817, -106.0384),
    'Gunnison':     (38.5458, -106.9253), 'Pagosa Springs': (37.2695, -107.0098),
    'Delta':        (38.7422, -108.0687), 'Aspen':       (39.1911, -106.8175),
}

# Past this a cell is not "near" a town in any sense a rep would accept; it is
# open county and the brief says so rather than naming somewhere ten miles off.
NEAR_MILES = 10.0


def _miles(lat1, lng1, lat2, lng2):
    import math
    return 69.0 * math.hypot(lat1 - lat2,
                             (lng1 - lng2) * math.cos(math.radians(lat1)))


def places(rects, near_miles=NEAR_MILES):
    """[(where, cells, max_size)] worst first.

    A cell belongs to its NEAREST town, so a swath between two of them is not
    double-counted into both and made to look twice its size.

    Cells further than `near_miles` from anywhere bucket as "open county near
    <town>" — ONE row per town, not one per distance. Keying on the distance
    instead put 1,348 cells on the eastern plains into forty near-identical
    rows, which is a table nobody reads; the distance is something the row
    SHOWS, never something it is grouped by.
    """
    hit = {}
    for s, w, n, e, size in rects:
        la, ln = (s + n) / 2.0, (w + e) / 2.0
        best, best_d = None, None
        for town, (tla, tln) in TOWNS.items():
            d = _miles(la, ln, tla, tln)
            if best_d is None or d < best_d:
                best, best_d = town, d
        near = best_d is not None and best_d <= near_miles
        key = (best or 'open county', near)
        cur = hit.setdefault(key, {'cells': 0, 'max_size': 0.0, 'nearest': best_d})
        cur['cells'] += 1
        cur['max_size'] = max(cur['max_size'], size)
        if best_d is not None:
            cur['nearest'] = min(cur['nearest'] if cur['nearest'] is not None
                                 else best_d, best_d)
    out = []
    for (town, near), v in hit.items():
        where = town if near else (
            f'open county near {town} (from {v["nearest"]:.0f} mi)'
            if v['nearest'] is not None else 'open county')
        out.append((where, v['cells'], round(v['max_size'], 2)))
    return sorted(out, key=lambda r: (-r[2], -r[1]))


def _esc(s):
    return (str(s).replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;'))


# How many rows a section prints. A statewide day runs to thousands of cells
# and the brief is a decision aid, not a data dump — the rows are sorted worst
# first, so a cap keeps the biggest hail and drops the tail.
SECTION_ROWS = 8


def _section(title, rects, note='', limit=SECTION_ROWS):
    rows = places(rects)
    more = max(0, len(rows) - limit)
    rows = rows[:limit]
    out = [f'<div style="margin:14px 0 4px;font-weight:600">{_esc(title)}</div>']
    if note:
        out.append(f'<div style="color:#555;font-size:13px;margin-bottom:8px">{note}</div>')
    out.append('<table cellpadding="6" style="border-collapse:collapse;font-size:14px">')
    for town, cells, mx in rows:
        out.append(
            f'<tr><td style="border-bottom:1px solid #eee"><b>{_esc(town)}</b></td>'
            f'<td style="border-bottom:1px solid #eee">{mx:.2f}&quot;</td>'
            f'<td style="border-bottom:1px solid #eee;color:#666">{cells} cell(s)</td></tr>')
    out.append('</table>')
    if more:
        # Said out loud rather than silently truncated: the same honesty rule
        # `cells_in` follows when it caps a viewport.
        out.append(f'<div style="color:#888;font-size:12px;margin-top:4px">'
                   f'&hellip; and {more} more place(s), smaller.</div>')
    return ''.join(out)


def brief_html(events, base_url=''):
    """The email. Every number is the archive's own; nothing is extrapolated.

    Two sections per storm, and the order is the point: what landed on the
    service area first, the rest of Colorado under it. A statewide brief that
    mixed them would bury the six cells over Platteville that a crew can be on
    by breakfast beneath three hundred on the eastern plains that nobody is
    driving to.
    """
    parts = ['<div style="font-family:system-ui,-apple-system,Segoe UI,sans-serif;'
             'max-width:640px">']
    for ev in events:
        head = (f'up to {ev["max_size"]:.2f}&quot; in the service area'
                if ev['cells'] else 'nothing in the service area')
        parts.append(
            f'<h2 style="margin:20px 0 2px">{_esc(ev["event_date"])} &mdash; {head}</h2>')
        if ev['cells']:
            parts.append(_section(
                'In the service area', ev['rects'],
                f'{ev["cells"]} radar cell(s). Each is about one square '
                f'kilometre of ground the radar made a claim about &mdash; not '
                f'a spotter call-in.'))
        if ev.get('away_cells'):
            parts.append(_section(
                'Elsewhere in Colorado', ev['away_rects'],
                f'{ev["away_cells"]} cell(s), up to {ev["away_max"]:.2f}&quot;. '
                f'Not ground we work today.'))
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

    # The subject answers the only question worth answering on a phone screen:
    # did it hit us. A statewide storm that missed the service area still gets
    # mailed — Luke asked for all of Colorado — but it must not read as though
    # it landed on the ground the crews work.
    worst = max(e['max_size'] for e in events)
    away = max((e.get('away_max') or 0) for e in events)
    when = events[0]['event_date'] if len(events) == 1 else \
        f'{events[-1]["event_date"]} to {events[0]["event_date"]}'
    subject = (f'🌩 Hail in the service area — {when}, up to {worst:.2f}"'
               if worst else
               f'🌩 Hail in Colorado, not our area — {when}, up to {away:.2f}"')
    if not send_email(subject, brief_html(events, base_url), to_addr):
        return {'sent': 0, 'events': [e['event_id'] for e in events],
                'note': 'send failed — left pending for the next run'}
    mark_sent(events)
    return {'sent': len(events), 'events': [e['event_id'] for e in events],
            'max_size': worst}
