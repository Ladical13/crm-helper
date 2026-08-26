"""NCES Common Core of Data — the free public-school source.

Served through the Urban Institute's Education Data API, which republishes
NCES CCD as JSON. Free, no key, no quota. NCES's own route is the ELSI table
generator (an interactive page) and a set of yearly zip files whose names
change every release; the API is the same federal data without either problem.

**This one carries phone numbers**, which is what makes it worth more than the
Perplexity fallback it replaces: 1,965 Colorado schools on one call, each with
a district, a street address, a phone and an enrollment count.

Enrollment is the closest thing to a free ICP signal in the whole pipeline —
on a roof, pupil count is a decent proxy for square footage — so it rides
along in the hook where a rep can see it, and nudges the pre-enrichment score.
"""
import json

from . import _common

API = 'https://educationdata.urban.org/api/v1/schools/ccd/directory/{year}/'

# The CCD release lags a couple of years; try recent years newest-first and
# take the first that answers, rather than pinning a year that will 404 later.
_YEARS = (2022, 2021, 2020)

# school_status 1 = open, 3 = new, 8 = reopened. 2 (closed), 4 (not in scope),
# 5 (inactive), 6 (future) and 7 (partial) are not places with a live roof.
_OPEN = {1, 3, 8}


def _district_name(raw):
    """The useful half of CCD's ``lea_name``.

    NCES stores names like "Academy School District No. 20 in the county of
    El Paso an" — the county clause is legal boilerplate, and NCES caps the
    field at about 60 characters, so it routinely arrives truncated mid-word.
    Cutting at the clause removes both problems at once and leaves the part a
    rep would actually recognise.
    """
    s = _common.clean(raw)
    for marker in (' in the county of', ' in the County of'):
        idx = s.find(marker)
        if idx > 0:
            s = s[:idx]
            break
    return s.strip(' ,&')


def _first(*vals):
    for v in vals:
        c = _common.clean(v)
        if c:
            return c
    return ''


def schools(city='', county='', state='CO', limit=None):
    """Open public schools for a state, richest first.

    Returns ``[]`` on any failure so the dispatcher falls through to
    Perplexity gap-fill, per the contract in ``sources/__init__``.
    """
    st = (state or 'CO').strip().upper()[:2]
    body = None
    for year in _YEARS:
        body = _common.fetch_cached(
            f'nces_ccd_{st}_{year}.json', API.format(year=year),
            params={'state_location': st})
        if body:
            break
    if not body:
        return []

    try:
        payload = json.loads(body)
    except ValueError:
        return []
    results = payload.get('results') if isinstance(payload, dict) else payload
    if not isinstance(results, list):
        return []

    rows = []
    for raw in results:
        if not isinstance(raw, dict):
            continue
        try:
            status = int(raw.get('school_status'))
        except (TypeError, ValueError):
            status = 1                     # absent status is not a reason to drop it
        if status not in _OPEN:
            continue

        # Prefer where the building IS over where its post reaches.
        address = _first(raw.get('street_location'), raw.get('street_mailing'))
        row_city = _first(raw.get('city_location'), raw.get('city_mailing'))
        if not address or _common.is_po_box(address):
            continue
        if city and not _common.matches_city(row_city, city):
            continue

        name = _common.title(_common.clean(raw.get('school_name')))
        ncessch = _common.clean(raw.get('ncessch'))
        if not name or not ncessch:
            continue

        phone = _common.clean(raw.get('phone'))
        try:
            enrollment = int(raw.get('enrollment'))
        except (TypeError, ValueError):
            enrollment = 0

        district = _district_name(raw.get('lea_name'))
        hook_bits = [b for b in (district,
                                 f'{enrollment} students' if enrollment > 0 else '')
                     if b]

        rows.append({
            'company':    name,
            'first_name': '',
            'last_name':  '',
            'phone':      phone,
            'email':      '',
            'website':    '',
            'address':    _common.title(address),
            'city':       _common.title(row_city),
            'state':      _first(raw.get('state_location'), st)[:2].upper() or st,
            'zip':        _first(raw.get('zip_location'),
                                 raw.get('zip_mailing')).split('-')[0],
            'source_ref': f'nces:{ncessch}',
            'license_no': ncessch,
            'hook':       ' · '.join(hook_bits)[:180],
            # Pupil count is a rough stand-in for roof area, and it is the one
            # size signal available before paying to enrich. Deliberately
            # small next to reachability (max 4 here vs 2 for a phone) so it
            # tips ties rather than burying a school somebody can call.
            'icp_score':  (_common.base_icp_score(phone=phone, address=address)
                           + (1 if enrollment >= 300 else 0)
                           + (1 if enrollment >= 800 else 0)),
            '_enrollment': enrollment,
        })

    rows.sort(key=lambda r: (-int(r.get('icp_score') or 0),
                             -int(r.get('_enrollment') or 0),
                             r.get('company', '')))
    for r in rows:
        r.pop('_enrollment', None)
    if limit:
        return rows[:int(limit)]
    return rows
