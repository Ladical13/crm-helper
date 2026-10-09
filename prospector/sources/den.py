"""The Den (Base44) — the people who already know us.

Every other source here is a list of strangers. This one is the Colorado
customers we have finished a roof for, the jobs we inspected or quoted that
never closed, and the referral partners already on file. They are the warmest
names the company has, and until this module they were in no outreach queue at
all: the CRM's queue held churches and schools, and the people who had already
paid us sat in the back office untouched.

Needs `BASE44_TOKEN`, which lives in Railway, so run it there:

    railway run --service project-one-estimator -- \
        python -m prospector pull den:customers --out prospector/inbox/den-customers.json

Rows carry four keys the open-data sources never do — `stage`, `won_at`,
`created_at`, `owner` — and each segment names how it should be imported
(`import` below). `prospector push` forwards both, and the CRM's importer
honours them only on a warm batch (salescrm `WARM_SOURCES`). `owner` is the
Den's salesperson; `push --owners derik` is what lets a rep keep their own
customers, and without it every row goes to `--assign`.

Three rules, each of which is a way to embarrass the company if it is missing:

  • Anyone with a job IN PRODUCTION is left out of every segment. A sales text
    in the middle of someone's install reads as the left hand not knowing what
    the right is doing.
  • Red-flag customers are left out.
  • One row per person. A customer with two finished jobs is one customer, and
    a customer is never also an "open job".
  • An open job is only "open" once it has sat for STALE_DAYS. Someone who
    asked for an inspection on Friday is being looked after by a person, and
    "I never heard where it landed" on Monday is not that.

Colorado only. The Den also holds the Texas market, about eight times the
size; those relationships belong to another team.
"""
import os
import re
from datetime import datetime, timezone

import requests

from .. import normalize

BASE = 'https://base44.app/api/apps/69320ef0c647fee442697971'
CO_LOCATION_ID = os.environ.get('CO_LOCATION_ID', '6984bb86d86d9c92d6827a17')

# Den project statuses, sorted into what they mean for outreach. A status in
# none of these (lost_or_cancelled, anything new) is left alone on purpose:
# guessing that an unknown status is "open" is how a cancelled customer gets a
# cheerful check-in.
CLOSED = {'paid_and_closed'}
IN_PRODUCTION = {'contracted', 'claim_filed', 'pull_permit', 'ready_for_production',
                 'scheduled', 'additional_trades', 'roof_complete', 'collect_final_payment'}
OPEN = {'new_lead', 'inspected', 'retail_estimate_needed', 'estimate_the_follow_up',
        'ready_to_close', 'follow_up', 'holding', 'no_damage_call_future'}

# How long a job sits untouched in the Den before outreach picks it up.
STALE_DAYS = 14

PARTNER_TYPES = {'realtor', 'insurance_agent', 'property_manager', 'adjuster', 'hoa'}

SEGMENTS = {
    'customers': {
        'label': 'Colorado customers with a finished, paid job',
        'lead_type': 'homeowner',
        'import': {'lead_source': 'existing_customer', 'cadence': 'past_customer_winter',
                   'stagger_per_day': 6},
        'note': 'Lands as stage won with the real completion date.',
    },
    'open_jobs': {
        'label': 'Colorado jobs inspected or quoted, never closed',
        'lead_type': 'homeowner',
        'import': {'lead_source': 'existing_customer', 'cadence': 'cold_revive',
                   'stagger_per_day': 6},
        'note': 'Lands as stage follow_up. Push den:customers first.',
    },
    'partners': {
        'label': 'Colorado referral partners already on file',
        'lead_type': 'referral_partner',
        'import': {'lead_source': 'referral', 'cadence': 'partner_nurture',
                   'stagger_per_day': 4},
        'note': 'Each row carries its own lead_type (realtor, insurance_agent, ...).',
    },
}


def _fetch(entity, location=True):
    """Every record of one Den entity. The live call; tests replace it."""
    token = os.environ.get('BASE44_TOKEN', '').strip()
    if not token:
        raise KeyError('BASE44_TOKEN is not set - run this under `railway run`')
    params = {'q': '{"location_id": "%s"}' % CO_LOCATION_ID} if location else {}
    r = requests.get(f'{BASE}/entities/{entity}', params=params, timeout=120,
                     headers={'Authorization': f'Bearer {token}'})
    if r.status_code in (401, 403):
        raise KeyError('The Den refused BASE44_TOKEN - it has probably expired')
    r.raise_for_status()
    return r.json()


def _digits(phone):
    d = re.sub(r'\D', '', phone or '')
    if len(d) == 11 and d.startswith('1'):
        d = d[1:]
    return d if len(d) == 10 else ''


def _keys(email, phone, name):
    """Every way two Den records can be the same person."""
    out = set()
    if (email or '').strip():
        out.add('e:' + email.strip().lower())
    if _digits(phone):
        out.add('p:' + _digits(phone))
    if not out and (name or '').strip():
        out.add('n:' + ' '.join(name.lower().split()))
    return out


def _split(name):
    """'Pat & Sam Ng' -> ('Pat', 'Ng'); a single word is a first name."""
    parts = [p for p in re.split(r'\s+', (name or '').strip()) if p]
    if not parts:
        return '', ''
    if len(parts) >= 3 and parts[1] in ('&', 'and'):
        return parts[0], ' '.join(parts[3:]) or parts[-1]
    return parts[0], ' '.join(parts[1:])


def _when(p, *fields):
    """The first of `fields` the project has a value for."""
    for f in fields:
        if str(p.get(f) or '').strip():
            return str(p[f]).strip()
    return ''


def _age_days(stamp, today=None):
    """Whole days since a Den timestamp; None when it cannot be read."""
    s = str(stamp or '').strip()
    if not s:
        return None
    try:
        dt = datetime.fromisoformat(s.replace('Z', '+00:00'))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return ((today or datetime.now(timezone.utc)) - dt).days


def _people(fetch):
    projects = [p for p in fetch('Project') if not p.get('is_sample')]
    contacts = [c for c in fetch('Contact') if not c.get('is_sample')]
    by_key = {}
    for c in contacts:
        for k in _keys(c.get('email'), c.get('phone'), c.get('name')):
            by_key.setdefault(k, c)

    def keys_of(p):
        return _keys(p.get('client_email'), p.get('client_phone'), p.get('client_name'))

    skip = set()
    for p in projects:
        if p.get('status') in IN_PRODUCTION:
            skip |= keys_of(p)
    for c in contacts:
        if c.get('is_red_flag_customer'):
            skip |= _keys(c.get('email'), c.get('phone'), c.get('name'))
    return projects, by_key, keys_of, skip


def _owner(*emails):
    """'derik@projectoneroofing.com' -> 'derik'. The Den's salesperson, as a
    portal username. Whether that person still works this market is not
    decided here: the importer keeps an owner only for reps it is told to."""
    for e in emails:
        name = str(e or '').strip().lower().split('@')[0]
        if name:
            return name
    return ''


def _row(p, contact, stage):
    first, last = _split(p.get('client_name') or (contact or {}).get('name') or p.get('name'))
    c = contact or {}
    city = (c.get('city') or '').strip().title()
    address = (c.get('street_address') or p.get('address') or '').strip()
    out = normalize.row(
        first_name=first, last_name=last,
        phone=p.get('client_phone') or c.get('phone'),
        email=(p.get('client_email') or c.get('email') or '').strip().lower(),
        address=address, city=city, state=(c.get('state') or 'CO').strip().upper()[:2],
        zip=str(c.get('zip_code') or '').strip()[:5],
        source_ref=f"den:contact:{c['id']}" if c.get('id') else f"den:project:{p.get('id')}",
        icp_score=normalize.score(city=city, address=address, person=first),
    )
    out['stage'] = stage
    out['owner'] = _owner(p.get('assigned_salesperson'), c.get('assigned_to'))
    out['created_at'] = _when(p, 'created_date')
    out['won_at'] = _when(p, 'roof_installation_completed_date', 'actual_end_date',
                          'updated_date') if stage == 'won' else ''
    return out


def _projects(segment, fetch, today=None):
    projects, by_key, keys_of, skip = _people(fetch)
    want, stage = (CLOSED, 'won') if segment == 'customers' else (OPEN, 'follow_up')
    customers = set()
    for p in projects:
        if p.get('status') in CLOSED:
            customers |= keys_of(p)
    seen = set()
    # Newest first, so the row kept for someone with two jobs is the recent one.
    for p in sorted(projects, key=lambda p: _when(p, 'updated_date', 'created_date'), reverse=True):
        ks = keys_of(p)
        if p.get('status') not in want or not ks or ks & skip or ks & seen:
            continue
        if segment == 'open_jobs':
            if ks & customers:
                continue
            # Newest first, so this is the person's most recent activity: if
            # that is fresh, somebody is already talking to them.
            age = _age_days(_when(p, 'updated_date', 'created_date'), today)
            if age is None or age < STALE_DAYS:
                seen |= ks
                continue
        seen |= ks
        contact = next((by_key[k] for k in sorted(ks) if k in by_key), None)
        yield _row(p, contact, stage)


def _partners(fetch):
    for r in fetch('ReferralPartner', location=False):
        if r.get('is_sample') or r.get('location_id') != CO_LOCATION_ID:
            continue
        if (r.get('status') or 'active').lower() not in ('active', ''):
            continue
        first, last = _split(r.get('name'))
        if not (first or (r.get('company') or '').strip()):
            continue
        ptype = (r.get('partner_type') or '').strip().lower()
        out = normalize.row(
            first_name=first, last_name=last, company=r.get('company'),
            phone=r.get('phone'), email=(r.get('email') or '').strip().lower(),
            state='CO', source_ref=f"den:partner:{r.get('id')}",
            icp_score=normalize.score(person=first),
        )
        out['lead_type'] = ptype if ptype in PARTNER_TYPES else 'referral_partner'
        out['owner'] = _owner(r.get('assigned_to'), r.get('assigned_salesperson'))
        out['created_at'] = str(r.get('created_date') or '').strip()
        yield out


def pull(segment, limit=None, fetch=None, today=None):
    """Yield prospect rows for one segment. `fetch(entity, location=True)` is
    the Den read and `today` the clock, both injectable so the tests never
    touch the network or depend on the date."""
    if segment not in SEGMENTS:
        raise KeyError(f'unknown Den segment {segment!r}')
    fetch = fetch or _fetch
    rows = _partners(fetch) if segment == 'partners' else _projects(segment, fetch, today)
    for n, r in enumerate(rows):
        if limit and n >= limit:
            return
        yield r


def count(segment, **kw):
    return sum(1 for _ in pull(segment, **kw))
