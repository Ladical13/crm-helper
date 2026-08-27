"""County assessor records — the free source for the `commercial` segment.

Who owns a building is a matter of public record, and both counties we work
publish it. This replaces asking a model to guess at "commercial building
owners in Greeley", which was the entire `commercial` pipeline before.

What each county gives, measured against the live files on 2026-08-26:

  **Larimer** — eight CSV extracts on Google Cloud Storage, no key. 10,061
  commercial improvements, every one carrying occupancy type, square footage,
  year built and condition, joined to the owner's name and mailing address.
  2,457 of them are flat-roofed; 837 of those are 10,000+ sq ft.

  **Weld** — an ArcGIS FeatureServer, queryable with a WHERE clause so we pull
  only the city asked for instead of downloading the county. 6,172 commercial
  accounts with owner name, business name, situs address and mailing address.

**Weld carries no roof or year-built data and Larimer's is partial.**
``ROOFTYPE`` (the shape — Flat/Gable/Shed) is filled for 68% of Larimer
commercial rows and is the useful one. ``ROOFCOVER`` (the material) is filled
for 98% of *residential* but only 3.6% of commercial, so it is read when
present and never relied on. Nothing here infers a roof it was not told about.

The owner is frequently an LLC whose mailing address is a registered agent or
a PO box. That is still the right lead — it is who signs — but it is a mailing
address, not a person, and `sos.py` is what turns the entity into a name.
"""
import csv
import json
import re

from . import _common

# ── Larimer ──────────────────────────────────────────────────────────────────

_LC = 'https://storage.googleapis.com/lc-public/asr/'
_LC_IMPROVEMENT = _LC + 'assessor-public-improvement.csv'
_LC_OWNER = _LC + 'assessor-public-owner-location.csv'

# ── Weld ─────────────────────────────────────────────────────────────────────

_WELD_QUERY = ('https://services.arcgis.com/ewjSqmSyHJnkfBLL/arcgis/rest/'
               'services/Ownership2/FeatureServer/17/query')
_WELD_FIELDS = ('ACCOUNTNO,NAME,BUSINESSNAME,ADDRESS1,ADDRESS2,CITY,STATE,'
                'ZIPCODE,STREETNO,STREETDIR,STREETNAME,STREETSUF,LOCCITY,'
                'SQFT,ACCTTYPE')

# Larimer labels the column PROPERTYTYPE; Weld calls it ACCTTYPE. Industrial
# roofs are the same sale as commercial ones, so Weld contributes both.
_WELD_TYPES = ("'Commercial','Industrial'")

# An owner with no name is not a lead, and these two appear as owner names on
# public parcels where the "owner" is the taxing entity itself.
_NOT_A_PROSPECT = re.compile(
    r'^(unknown|none|n/?a|to be determined|tbd)$', re.I)


def _flat_roof(rooftype):
    return 'flat' in (rooftype or '').lower()


def _score(*, sf, year, rooftype, has_phone=False):
    """Rank a commercial building by how much roof is on it and how old.

    Deliberately additive on top of ``base_icp_score`` rather than replacing
    it, so a commercial row sorts against a church or a school in the same
    queue without a second scale. Square footage is the closest thing to
    contract value available for free, and a flat roof on a big old building
    is the single most re-roofable thing in the dataset.
    """
    score = 0
    if sf >= 50000:
        score += 3
    elif sf >= 20000:
        score += 2
    elif sf >= 10000:
        score += 1
    if _flat_roof(rooftype):
        score += 2
    # Asphalt and single-ply both age out around here. Year 0 means unknown,
    # which must not read as "very old" and jump the queue.
    if year and year <= 2000:
        score += 2
    elif year and year <= 2010:
        score += 1
    return score


def _int(v):
    try:
        return int(float(str(v).strip() or 0))
    except (TypeError, ValueError):
        return 0


def _larimer_commercial_index(path, want_city):
    """ACCOUNTNO -> the biggest commercial improvement on that account.

    An account can carry several improvements (a warehouse plus its office).
    The largest is the one that decides the job, and pushing one lead per
    building would put the same owner in the queue five times.
    """
    index = {}
    with open(path, encoding='utf-8', errors='replace', newline='') as f:
        for row in csv.DictReader(f):
            if (row.get('PROPERTYTYPE') or '').strip() != 'Commercial':
                continue
            acct = (row.get('ACCOUNTNO') or '').strip()
            if not acct:
                continue
            sf = _int(row.get('SF'))
            prev = index.get(acct)
            if prev and prev['sf'] >= sf:
                continue
            index[acct] = {
                'sf': sf,
                'occ': _common.clean(row.get('OCCDESCRIPTION')),
                'year': _int(row.get('BLTASYEARBUILT')),
                'rooftype': _common.clean(row.get('ROOFTYPE')),
                'roofcover': _common.clean(row.get('ROOFCOVER')),
                'condition': _common.clean(row.get('IMPCONDITIONTYPE')),
            }
    return index


def _larimer(city, limit):
    imp_path = _common.fetch_cached_path('larimer_improvement.csv',
                                         _LC_IMPROVEMENT)
    own_path = _common.fetch_cached_path('larimer_owner_location.csv',
                                         _LC_OWNER)
    if not imp_path or not own_path:
        return []

    index = _larimer_commercial_index(imp_path, city)
    if not index:
        return []

    rows = []
    with open(own_path, encoding='utf-8', errors='replace', newline='') as f:
        for row in csv.DictReader(f):
            acct = (row.get('ACCOUNTNO') or '').strip()
            hit = index.get(acct)
            if not hit:
                continue
            situs_city = _common.clean(row.get('SITUSCITY'))
            if city and not _common.matches_city(situs_city, city):
                continue
            address = _common.clean(row.get('SITUSADDRESS'))
            if not address or _common.is_po_box(address):
                continue          # the building has to be somewhere real
            owner = _common.clean(row.get('NAME1'))
            if not owner or _NOT_A_PROSPECT.match(owner):
                continue

            mail = ' '.join(x for x in (_common.clean(row.get('MAILADDRESS1')),
                                        _common.clean(row.get('MAILADDRESS2')))
                            if x)
            rows.append(_build(
                owner=owner, address=address, city=situs_city,
                zip_code=_common.clean(row.get('SITUSZIPCODE')),
                mail=mail,
                mail_city=_common.clean(row.get('MAILCITY')),
                mail_state=_common.clean(row.get('MAILSTATE')),
                mail_zip=_common.clean(row.get('MAILZIPCODE')),
                source_ref=f'assessor:larimer:{acct}',
                account=acct, county='Larimer', **hit))
            if limit and len(rows) >= int(limit) * 4:
                break             # sorted below; keep a margin before cutting
    return rows


def _weld(city, limit):
    if not city:
        return []
    # Escape the quote the ArcGIS WHERE clause is built with. A city name has
    # no business containing one, but a source that builds SQL from a caller's
    # string should not be the reason we find out.
    safe = str(city).replace("'", "''").upper()
    where = (f"ACCTTYPE IN ({_WELD_TYPES}) AND UPPER(LOCCITY) = '{safe}'")
    body = _common.fetch_cached(
        f'weld_commercial_{re.sub(r"[^a-z0-9]+", "_", city.lower())}.json',
        _WELD_QUERY,
        params={'where': where, 'outFields': _WELD_FIELDS, 'f': 'json',
                'resultRecordCount': 4000, 'returnGeometry': 'false'})
    if not body:
        return []
    try:
        features = (json.loads(body) or {}).get('features') or []
    except ValueError:
        return []

    rows = []
    for feat in features:
        a = feat.get('attributes') or {}
        owner = _common.clean(a.get('NAME'))
        if not owner or _NOT_A_PROSPECT.match(owner):
            continue
        address = ' '.join(x for x in (
            _common.clean(a.get('STREETNO')), _common.clean(a.get('STREETDIR')),
            _common.clean(a.get('STREETNAME')), _common.clean(a.get('STREETSUF')))
            if x)
        if not address or _common.is_po_box(address):
            continue
        acct = _common.clean(a.get('ACCOUNTNO'))
        # Weld publishes no roof or year-built data at all. Leave both empty
        # rather than inventing a default that would score as if we knew.
        rows.append(_build(
            owner=owner, address=address,
            city=_common.clean(a.get('LOCCITY')) or city,
            zip_code='', mail=' '.join(x for x in (
                _common.clean(a.get('ADDRESS1')),
                _common.clean(a.get('ADDRESS2'))) if x),
            mail_city=_common.clean(a.get('CITY')),
            mail_state=_common.clean(a.get('STATE')),
            mail_zip=_common.clean(a.get('ZIPCODE')),
            source_ref=f'assessor:weld:{acct}', account=acct, county='Weld',
            sf=_int(a.get('SQFT')),
            occ=_common.clean(a.get('BUSINESSNAME')),
            year=0, rooftype='', roofcover='', condition=''))
    return rows


def _build(*, owner, address, city, zip_code, mail, mail_city, mail_state,
           mail_zip, source_ref, account, county, sf, occ, year, rooftype,
           roofcover, condition):
    # The owner's mailing address has to ride in the hook: the salescrm
    # importer accepts a fixed field list (PROSPECT_TEXT_FIELDS) and silently
    # ignores anything else, so a `mailing_address` key would vanish without a
    # word. It earns the space — for a commercial LLC the building has no
    # mailbox, and a letter to the tax address reaches whoever signs.
    mailing = ', '.join(x for x in (
        _common.title(mail), _common.title(mail_city),
        (mail_state or '').upper(), (mail_zip or '')[:5]) if x)
    bits = [b for b in (
        occ,
        f'{sf:,} sq ft' if sf else '',
        f'built {year}' if year else '',
        f'{rooftype} roof' if rooftype else '',
        roofcover,
        f'Owner mail: {mailing}' if mailing else '',
    ) if b]
    return {
        'company':    _common.title(owner),
        'first_name': '',
        'last_name':  '',
        'phone':      '',
        'email':      '',
        'website':    '',
        'address':    _common.title(address),
        'city':       _common.title(city),
        'state':      'CO',
        'zip':        (zip_code or '').split('-')[0][:5],
        'source_ref': source_ref,
        'license_no': account,
        'hook':       _common.clip(' · '.join(bits), 180),
        'icp_score':  (_common.base_icp_score(address=address)
                       + _score(sf=sf, year=year, rooftype=rooftype)),
        '_sf': sf,
    }


def commercial(city='', county='', state='CO', limit=None):
    """Commercial property owners for a city, best roof first.

    Both counties are asked: a city sits in one of them, and the other simply
    returns nothing, which is cheaper than maintaining a city→county table
    that would be wrong for Windsor (it straddles the line).

    Returns ``[]`` on any failure so the dispatcher falls through to
    Perplexity, per the contract in ``sources/__init__``.
    """
    if (state or 'CO').upper()[:2] != 'CO':
        return []
    rows = []
    for fn in (_larimer, _weld):
        try:
            rows.extend(fn(city, limit))
        except Exception:                                        # noqa: BLE001
            continue          # one county failing must not lose the other

    # One owner can hold many parcels. Keeping every one would bury a rep in
    # the same name; keeping the biggest building is the call worth making.
    best = {}
    for r in rows:
        key = (r['company'], r['city'])
        if key not in best or best[key]['_sf'] < r['_sf']:
            best[key] = r
    out = sorted(best.values(),
                 key=lambda r: (-int(r['icp_score']), -int(r['_sf']),
                                r['company']))
    for r in out:
        r.pop('_sf', None)
    out = out[:int(limit)] if limit else out

    # Put a person's name to the LLC, but only for the rows actually being
    # returned — resolving every commercial parcel in a county would be
    # thousands of lookups to answer a question nobody asked.
    try:
        from . import sos
        found = sos.principals_for([r['company'] for r in out])
        for r in out:
            who = found.get(r['company'])
            if who:
                r['first_name'] = who['first_name']
                r['last_name'] = who['last_name']
                # Reachability improved: there is now somebody to ask for.
                r['icp_score'] = int(r['icp_score']) + 1
    except Exception:                                            # noqa: BLE001
        pass          # an unresolved entity is still a perfectly good lead
    return out
