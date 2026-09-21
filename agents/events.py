"""Local networking events, ranked by who is actually in the room.

A list of events near Fort Collins is a Google search. What makes this worth
running is the second half: Nimbus knows which partner segments the CRM is
THIN on, so it can say that the insurance-agent mixer is worth an evening and
the realtor coffee — the fortieth realtor contact this year — is not.

That is the whole design. Proximity is a tiebreak; the partner gap is the
score.

Four rules carry the rest, and each is a way this feature fails quietly:

**An event has a date, and a list of events DECAYS.** Past events are dropped
at READ time, never only at fetch. A search cached ten days ago still holds
everything it found, and the failure nobody forgives is Nimbus confidently
telling Luke to attend something that happened last Tuesday. `upcoming()` is
the only read path for that reason.

**An event with no date is not an event.** It is a webpage. A row that cannot
be put in a calendar cannot be acted on, and — worse — a row with no date can
never expire, so it sits at the top of the list forever. Dropped on the way in.

**Every event carries a URL or it is dropped.** Same non-negotiable the B2B
sources already apply: the citation is the defence against a model inventing a
chamber breakfast. It is also just practical — an event Luke cannot click
through to register for is not one he is going to.

**A decision is STICKY.** Marking an event skipped must survive the next run.
A re-run refreshes the facts — time, venue, cost — and never touches
`decision`, for the same reason `save_estimate` re-applies a customer's
accepted upgrade: the fact is ours to update, the choice is not.
"""
import json
import re
from datetime import datetime

from portal import clock

from . import config
from . import perplexity


# Events are volatile in a way org names and addresses are not: a fortnight-old
# answer has stale times, moved venues and events that already happened. The
# global default is 30 days and would be wrong here by an order of magnitude.
CACHE_TTL_DAYS = 3

# How far ahead to look. Past about six weeks the listings thin out and the
# answers drift into "the chamber usually does something in spring".
HORIZON_DAYS = 45

# One clock for the company, shared with the other three apps. This module had
# its own zoneinfo lookup for about a day; the estimator's was written weeks
# earlier and said the same thing in slightly different words, which is how two
# answers to "what day is it" start to drift.
COMPANY_TZ = clock.COMPANY_TZ

# The CRM partner types an event audience can be expressed in. Kept as a plain
# tuple rather than imported from salescrm: `agents` talks to the CRM through
# its HTTP API and never reaches into its module or its database.
PARTNER_TYPES = (
    'realtor', 'hoa', 'insurance_agent', 'property_manager', 'adjuster',
    'referral_partner', 'gc',
)

_AUDIENCE_WORDS = {
    'realtor':          ('realtor', 'real estate', 'broker', 'nar ', 'mls'),
    'insurance_agent':  ('insurance', 'agent', 'underwrit', 'carrier'),
    'adjuster':         ('adjuster', 'claims', 'restoration'),
    'property_manager': ('property manag', 'apartment', 'multi-family',
                         'multifamily', 'rental'),
    'hoa':              ('hoa', 'homeowners association', 'community associ',
                         'cai '),
    'gc':               ('contractor', 'builder', 'construction', 'hba ',
                         'home builders'),
    'referral_partner': ('chamber', 'bni', 'networking', 'referral',
                         'business after hours', 'mixer'),
}


# Today in Colorado, not in UTC — the server runs in UTC, which from 6pm
# Mountain is already tomorrow there. With no tz database `clock` falls back to
# UTC, which can only drop an event EARLY: Luke loses tonight's mixer from the
# list a few hours before it starts. That is the right direction to fail here,
# because a missed event costs an evening and a past event shown as upcoming
# costs the page its credibility.
company_today = clock.company_today


_DATE_RE = re.compile(r'^(\d{4})-(\d{2})-(\d{2})')


def clean_date(v):
    """'YYYY-MM-DD' local wall clock, or '' if it is not a real date.

    Stored WITHOUT a timezone, like the canvasser's appointment: a 7am chamber
    breakfast in Fort Collins is at 7am in Fort Collins, and converting it on
    the way in is how it becomes a different day for six hours of every day.
    """
    m = _DATE_RE.match(str(v or '').strip())
    if not m:
        return ''
    try:
        return datetime(int(m.group(1)), int(m.group(2)), int(m.group(3))).date().isoformat()
    except ValueError:
        return ''


def event_key(name, starts_at):
    """Dedupe key. The same chamber breakfast found twice is one event.

    Name plus date rather than a URL: two sources describe one event with two
    links, and a monthly re-run must not stack a second copy of every recurring
    meeting on the list.
    """
    slug = re.sub(r'[^a-z0-9]+', '-', str(name or '').lower()).strip('-')
    return f'{slug}|{starts_at}'


def audience_from(text):
    """CRM partner types this event's blurb suggests will be in the room.

    A keyword read of the model's own words, kept here rather than asked of the
    model so the vocabulary is one reviewable list instead of a phrasing that
    drifts per call. Empty is a fine answer and scores as one.
    """
    low = ' ' + str(text or '').lower() + ' '
    return sorted(t for t, words in _AUDIENCE_WORDS.items()
                  if any(w in low for w in words))


def score(event, partner_counts=None):
    """(score, why) for one event. Pure — pass the counts in.

    `partner_counts` is {lead_type: active_partner_count} from the CRM. It is
    an argument rather than a lookup so this stays testable with no database,
    the same reason `commercial_fastening` takes its table.

    The gap term is the point of the whole module. An audience the CRM is thin
    on is worth more than one it is saturated with: the fortieth realtor
    contact this year is not worth an evening and the second insurance agent
    is. With no counts every audience scores the same and this degrades to
    "events with a clear audience, in our area" — still useful, just not smart.
    """
    counts = {k: int(v or 0) for k, v in (partner_counts or {}).items()}
    audience = event.get('audience') or []
    why = []

    # A named audience at all. An event whose blurb could describe a bake sale
    # is worth less than one that says who it is for.
    base = 10.0 * len(audience)
    if audience:
        why.append('reaches ' + ', '.join(a.replace('_', ' ') for a in audience))

    gap = 0.0
    # Only segments the CRM actually REPORTED on. A type missing from the
    # counts is a type we have no evidence about, and scoring it as zero would
    # invent a gap — then say "you have 0 referral partners" about a number
    # nobody supplied. Absent is not empty.
    known = [a for a in audience if a in counts]
    if known:
        # Scarcest-first. The busiest segment sets the scale, so this stays
        # bounded however lopsided the pipeline gets.
        busiest = max(counts.values()) or 1
        for a in known:
            gap += 25.0 * (1.0 - min(counts[a], busiest) / busiest)
        have, thinnest = min((counts[a], a) for a in known)
        if have <= max(1, busiest // 10):
            why.append(f'only {have} {thinnest.replace("_", " ")} '
                       f'in the pipeline — your thinnest segment here')

    area = 0.0
    if event.get('in_service_area'):
        area = 8.0
        why.append('in the service area')

    # Mild, deliberately. A $95 dinner that puts you in front of ten property
    # managers is still the right evening; this only breaks ties.
    cost_penalty = 0.0
    dollars = _dollars(event.get('cost'))
    if dollars is not None:
        cost_penalty = min(dollars / 25.0, 8.0)
        if dollars == 0:
            why.append('free')

    total = round(base + gap + area - cost_penalty, 1)
    return max(total, 0.0), '; '.join(why)


_MONEY = re.compile(r'\$\s*([\d,]+(?:\.\d{2})?)')


def _dollars(v):
    """The number in a cost string, or None when it does not say.

    None and 0 are different answers — 'free' is a real fact about an event and
    'the page did not say' is not, and treating the second as free would flatter
    every event nobody priced.
    """
    s = str(v or '').strip().lower()
    if not s:
        return None
    if 'free' in s or 'no charge' in s or 'complimentary' in s:
        return 0.0
    m = _MONEY.search(s)
    if m:
        try:
            return float(m.group(1).replace(',', ''))
        except ValueError:
            return None
    return None


# ── Finding them ────────────────────────────────────────────────────────────

SYSTEM = (
    'You are a research assistant for a Colorado roofing contractor looking '
    'for business networking events worth attending. Every event must come '
    'from a real public listing you can cite. Never invent an event, a date, '
    'a venue or a price. If a listing does not give a specific date, skip it '
    'entirely rather than guessing — a date you are unsure of is worse than '
    'no event at all.'
)

PROMPT = (
    'Find business networking events, mixers, chamber of commerce events, '
    'BNI or referral-group meetings, trade association meetings and industry '
    'luncheons happening in {city}, Colorado between {start} and {end}.\n\n'
    'Prioritise events likely to be attended by: real estate agents and '
    'brokers, insurance agents, insurance adjusters, property managers, '
    'HOA and community association managers, apartment and multi-family '
    'operators, home builders and general contractors.\n\n'
    'For EACH event return: name, host (the organisation running it), '
    'date (STRICT ISO format YYYY-MM-DD), time (HH:MM 24-hour, or empty), '
    'city, venue, cost (e.g. "Free", "$25", or empty if not stated), '
    'url (the public listing or registration page), and a one-sentence '
    'summary naming WHO attends.\n\n'
    'Skip any event without a specific date or a working public URL. '
    'Return at most {limit} events.'
)


def find(city, today=None, limit=12, horizon_days=HORIZON_DAYS,
         model=None, force_refresh=False):
    """Search one city. Returns normalized rows — already date- and URL-filtered.

    Raises `perplexity.SpendCapReached` / `PerplexityError` the same way every
    other Nimbus source does; the caller decides whether one bad city sinks a
    whole run.
    """
    from datetime import timedelta
    today = today or company_today()
    end = today + timedelta(days=int(horizon_days))
    prompt = PROMPT.format(city=city, start=today.isoformat(),
                           end=end.isoformat(), limit=int(limit))
    result = perplexity.search_json(
        prompt, system=SYSTEM, model=model, max_tokens=2500,
        cache_ttl_days=CACHE_TTL_DAYS, force_refresh=force_refresh,
        reason=f'events:{city}')
    return normalize_all(result.get('data'), city=city, today=today,
                         horizon_days=horizon_days)


def normalize_all(data, city='', today=None, horizon_days=HORIZON_DAYS):
    """Model output -> clean rows. Everything that cannot be trusted is dropped."""
    from datetime import timedelta
    today = today or company_today()
    horizon = today + timedelta(days=int(horizon_days))
    rows, seen = [], set()
    for raw in _as_list(data):
        row = _normalize(raw, city=city)
        if not row:
            continue
        d = datetime.fromisoformat(row['starts_at']).date()
        # Past and far-future both dropped: one is a broken promise, the other
        # is the model padding the list with "they usually do this in spring".
        if d < today or d > horizon:
            continue
        if row['event_key'] in seen:
            continue
        seen.add(row['event_key'])
        rows.append(row)
    return rows


def _as_list(data):
    if isinstance(data, list):
        return [r for r in data if isinstance(r, dict)]
    if isinstance(data, dict):
        for k in ('events', 'results', 'items', 'rows', 'data'):
            v = data.get(k)
            if isinstance(v, list):
                return [r for r in v if isinstance(r, dict)]
    return []


_TIME_RE = re.compile(r'^([01]?\d|2[0-3]):([0-5]\d)')


def _normalize(raw, city=''):
    """One row, or None. A row missing a name, a real date or a URL is None."""
    name = _s(raw.get('name') or raw.get('title') or raw.get('event'))
    starts_at = clean_date(raw.get('date') or raw.get('starts_at')
                           or raw.get('start_date') or raw.get('when'))
    url = _s(raw.get('url') or raw.get('link') or raw.get('source_url')
             or raw.get('registration_url'))
    if not name or not starts_at or not url.lower().startswith('http'):
        return None

    tm = _TIME_RE.match(_s(raw.get('time') or raw.get('starts_time')))
    summary = _s(raw.get('summary') or raw.get('description'))
    host = _s(raw.get('host') or raw.get('organizer') or raw.get('organization'))
    return {
        'event_key':   event_key(name, starts_at),
        'name':        name[:200],
        'host':        host[:160],
        'starts_at':   starts_at,
        'starts_time': f'{tm.group(1).zfill(2)}:{tm.group(2)}' if tm else '',
        'city':        (_s(raw.get('city')) or city)[:80],
        'venue':       _s(raw.get('venue') or raw.get('location'))[:200],
        'url':         url[:500],
        'cost':        _s(raw.get('cost') or raw.get('price'))[:60],
        'summary':     summary[:600],
        # Read off the blurb AND the host: "Fort Collins Board of Realtors"
        # names its audience in the host line and nowhere else.
        'audience':    audience_from(f'{name} {host} {summary}'),
    }


def _s(v):
    if v is None:
        return ''
    s = str(v).strip()
    return '' if s.lower() in ('unknown', 'n/a', 'na', 'none', 'tbd', '-') else s


# ── Keeping them ────────────────────────────────────────────────────────────

# The facts a re-run is allowed to refresh. `decision`, `decided_by` and
# `decided_at` are deliberately absent: a venue moves and a price changes, and
# neither is a reason to un-skip an event Luke already looked at and passed on.
REFRESHABLE = ('name', 'host', 'starts_time', 'city', 'venue', 'url', 'cost',
               'summary', 'audience', 'in_service_area', 'score', 'score_why')


def record(rows, partner_counts=None, service_cities=None):
    """Upsert rows. Returns {'added', 'updated'}.

    Scored here rather than at read time so the number a manager sorted by is
    the number they saw. A re-score happens on the next run, which is also when
    the partner counts it depends on have moved.
    """
    cities = {c.strip().lower() for c in (service_cities or []) if c and c.strip()}
    added = updated = 0
    now = config.now_iso()
    with config.get_cache_db() as db:
        for row in rows:
            row = dict(row)
            row['in_service_area'] = int(
                not cities or (row.get('city') or '').strip().lower() in cities)
            row['score'], row['score_why'] = score(row, partner_counts)
            audience_json = json.dumps(row.get('audience') or [])

            existing = db.execute(
                'SELECT id FROM networking_events WHERE event_key=?',
                (row['event_key'],)).fetchone()
            if existing:
                sets, params = [], []
                for col in REFRESHABLE:
                    sets.append(f'{col}=?')
                    params.append(audience_json if col == 'audience' else row.get(col, ''))
                sets.append('updated_at=?'); params.append(now)
                params.append(existing['id'])
                db.execute(
                    f'UPDATE networking_events SET {", ".join(sets)} WHERE id=?', params)
                updated += 1
            else:
                db.execute(
                    'INSERT INTO networking_events '
                    '(event_key,name,host,starts_at,starts_time,city,venue,url,cost,'
                    ' audience,summary,in_service_area,score,score_why,found_at,updated_at) '
                    'VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                    (row['event_key'], row['name'], row.get('host', ''),
                     row['starts_at'], row.get('starts_time', ''), row.get('city', ''),
                     row.get('venue', ''), row.get('url', ''), row.get('cost', ''),
                     audience_json, row.get('summary', ''), row['in_service_area'],
                     row['score'], row['score_why'], now, now))
                added += 1
    return {'added': added, 'updated': updated}


def upcoming(today=None, limit=60, include_skipped=False):
    """Events still ahead of us, best first. The ONLY read path.

    Past events are filtered HERE rather than only at fetch, because the table
    outlives the search that filled it: a row stored three weeks ago is still
    in the table on the day it happens and the day after. Nothing else may read
    `networking_events` directly, or that guarantee lasts exactly as long as
    the next caller who forgets.
    """
    today = (today or company_today()).isoformat()
    clauses = ['starts_at >= ?']
    params = [today]
    if not include_skipped:
        clauses.append("decision != 'skipped'")
    with config.get_cache_db() as db:
        rows = db.execute(
            f'SELECT * FROM networking_events WHERE {" AND ".join(clauses)} '
            f'ORDER BY score DESC, starts_at ASC LIMIT ?', params + [int(limit)]
        ).fetchall()
    return [_row(r) for r in rows]


def _row(r):
    d = dict(r)
    d['audience'] = json.loads(d.get('audience') or '[]')
    d['in_service_area'] = bool(d.get('in_service_area'))
    return d


DECISIONS = ('', 'going', 'skipped')


def decide(event_id, decision, user=''):
    """Record going/skipped. Returns the row, or None if the id is unknown."""
    if decision not in DECISIONS:
        raise ValueError(f'decision must be one of {DECISIONS}')
    with config.get_cache_db() as db:
        db.execute(
            'UPDATE networking_events SET decision=?, decided_by=?, decided_at=? '
            'WHERE id=?',
            (decision, user or '', config.now_iso() if decision else '', int(event_id)))
        row = db.execute('SELECT * FROM networking_events WHERE id=?',
                         (int(event_id),)).fetchone()
    return _row(row) if row else None


def purge_past(today=None, keep_days=90):
    """Drop events long past. `upcoming()` already hides them; this is hygiene.

    Deliberately not run from `upcoming()`: a read that deletes is a read that
    behaves differently the second time, and the canvasser's `hail_cache` is
    already on the known-gaps list for growing forever with nothing to purge it.
    """
    from datetime import timedelta
    cutoff = ((today or company_today()) - timedelta(days=int(keep_days))).isoformat()
    with config.get_cache_db() as db:
        cur = db.execute('DELETE FROM networking_events WHERE starts_at < ?', (cutoff,))
        return cur.rowcount


# ── The run ─────────────────────────────────────────────────────────────────

def run(cities, partner_counts=None, service_cities=None, today=None,
        limit_per_city=12, model=None, force_refresh=False):
    """Search every city, store what comes back. Never raises for one bad city.

    One city failing — a timeout, a malformed answer — must not cost the other
    five, so each is caught and reported. The spend cap is the one exception:
    it stops the whole run, because every remaining city would hit it too and
    six identical failures is noise.
    """
    out = {'ok': True, 'cities': [], 'added': 0, 'updated': 0, 'errors': [],
           'stopped_early': ''}
    for city in cities or []:
        try:
            rows = find(city, today=today, limit=limit_per_city, model=model,
                        force_refresh=force_refresh)
        except perplexity.SpendCapReached as e:
            out['stopped_early'] = str(e)
            out['ok'] = False
            break
        except Exception as e:
            out['errors'].append({'city': city, 'error': str(e)})
            continue
        counts = record(rows, partner_counts=partner_counts,
                        service_cities=service_cities)
        out['added'] += counts['added']
        out['updated'] += counts['updated']
        out['cities'].append({'city': city, 'found': len(rows), **counts})
    return out


def rescore(partner_counts, today=None):
    """Re-rank stored events against today's pipeline. Returns how many moved.

    The scheduled run has no session, so it cannot read the CRM and its scores
    are the honest but blunt kind — audience and area only. This is the second
    half, run explicitly from the dashboard once counts are in hand.

    A separate, deliberate WRITE rather than scoring inside the read: a GET
    that quietly rewrites rows behaves differently the second time it is
    called, and the number a manager sorted by should be a number somebody
    stored on purpose.
    """
    today = (today or company_today()).isoformat()
    changed = 0
    with config.get_cache_db() as db:
        rows = db.execute(
            'SELECT * FROM networking_events WHERE starts_at >= ?', (today,)).fetchall()
        for r in rows:
            ev = _row(r)
            new_score, why = score(ev, partner_counts)
            if abs(new_score - (r['score'] or 0)) < 0.05 and why == (r['score_why'] or ''):
                continue
            db.execute('UPDATE networking_events SET score=?, score_why=?, updated_at=? '
                       'WHERE id=?', (new_score, why, config.now_iso(), r['id']))
            changed += 1
    return changed
