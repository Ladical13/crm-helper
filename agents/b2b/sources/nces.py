"""Public school directory — free, via the Urban Institute Education Data API.

Wraps NCES's Common Core of Data. Urban Institute republishes CCD as a clean
JSON API with no key and no registration, which is why it is preferred over
scraping the ELSI table generator.

Schools are a strong roofing segment for a reason worth stating: a district
owns many large flat roofs, replaces them on a capital schedule rather than
after a storm, and buys through a process that rewards being known early.

**Limit:** CCD covers PUBLIC schools only. Private and parochial schools are
absent, so the dispatcher's Perplexity fall-through still earns its place for
that half of the segment.

Fails safe: any network, HTTP or parse problem returns ``[]`` and the caller
falls through to Perplexity gap-fill — the behaviour before this was wired.
"""
import os

try:
    import requests
except ImportError:                            # pragma: no cover - dev only
    requests = None


BASE = os.environ.get('NCES_API_BASE',
                      'https://educationdata.urban.org/api-v1/schools/ccd/directory')
# CCD lags; the API 404s on a year it has not published yet, so this is
# overridable rather than computed from today's date.
YEAR = os.environ.get('NCES_CCD_YEAR', '2022')

STATE_FIPS = {'CO': 8}
TIMEOUT = 60
MAX_PAGES = 20            # a hard stop; CO is ~2k schools at 100/page


def _norm(s):
    return ' '.join(str(s or '').split()).strip()


def schools(city='', county='', state='CO', limit=None, session=None):
    """Return public-school rows in the salescrm importer shape."""
    if requests is None:
        return []
    fips = STATE_FIPS.get((state or 'CO').upper())
    if fips is None:
        return []

    sess = session or requests
    url = f'{BASE}/{YEAR}/'
    params = {'fips': fips}
    want_city   = _norm(city).lower()
    want_county = _norm(county).lower()

    out, pages = [], 0
    while url and pages < MAX_PAGES:
        pages += 1
        try:
            r = sess.get(url, params=params, timeout=TIMEOUT)
            if getattr(r, 'status_code', 0) != 200:
                break
            body = r.json() or {}
        except Exception as e:                 # pragma: no cover - network only
            print(f'[nces] fetch failed after {len(out)} rows: {e}')
            break

        for rec in (body.get('results') or []):
            row = _row(rec, state)
            if not row:
                continue
            if want_city and row['city'].lower() != want_city:
                continue
            if want_county and _norm(rec.get('county_name')).lower() != want_county:
                continue
            out.append(row)
            if limit and len(out) >= int(limit):
                return _ranked(out)

        url = body.get('next') or ''
        params = None                          # `next` already carries the query
    return _ranked(out)


def _row(rec, state):
    name = _norm(rec.get('school_name'))
    # NCES school id is the stable dedupe key — most rows carry no email, and a
    # school's phone often reaches a district switchboard shared by many rows.
    ncessch = _norm(rec.get('ncessch'))
    if not name or not ncessch:
        return None
    return {
        'company':    name,
        'first_name': '', 'last_name': '',
        'phone':      _norm(rec.get('phone')),
        'email':      '',
        'website':    '',
        'address':    _norm(rec.get('street_mailing') or rec.get('street_location')),
        'city':       _norm(rec.get('city_mailing') or rec.get('city_location')),
        'state':      (_norm(rec.get('state_mailing')) or state or 'CO')[:2].upper(),
        'zip':        _norm(rec.get('zip_mailing') or rec.get('zip_location')).split('-')[0],
        'license_no': '',
        'source_ref': f'nces:ccd:{ncessch}',
        'icp_score':  _score(rec),
    }


def _score(rec):
    """0-6. Enrollment is the honest proxy for roof area, and a bigger building
    is both a bigger job and a client with a real facilities budget."""
    n = 0
    if _norm(rec.get('street_mailing') or rec.get('street_location')):
        n += 2
    if _norm(rec.get('phone')):
        n += 1
    try:
        enroll = float(rec.get('enrollment') or 0)
        if enroll >= 800:
            n += 3
        elif enroll >= 300:
            n += 2
        elif enroll > 0:
            n += 1
    except (TypeError, ValueError):
        pass
    return min(n, 6)


def _ranked(rows):
    rows.sort(key=lambda r: -r['icp_score'])
    return rows
