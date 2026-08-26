"""IRS Exempt Organizations Business Master File — the free churches source.

The IRS publishes one CSV per state listing every exempt organization with its
EIN, name and address. Free, no key, no quota, authoritative, and the EIN is a
far better dedupe key than anything a model can produce.

Measured against the live Colorado file on 2026-08-26: 36,204 rows, of which
4,366 are active congregations and 3,567 of those carry a usable street
address. That replaces a Perplexity call per city that was returning ten
model-written rows, four in five with no phone number.

**What this does NOT give you: a phone number, an email, or a website.** The
BMF carries none of the three. Churches arrive as a name and a door — which is
how partner development at a church actually starts — and `enrich.py` is what
adds anything more.

One honest limit: the address is the organization's *mailing* address, which
for a small congregation is often the treasurer's house rather than the
building. A row whose city looks wrong for its name is usually this, not a
parse error.
"""
import csv
import io
import re

from . import _common

BASE = 'https://www.irs.gov/pub/irs-soi/eo_{state}.csv'

# X20 Christianity / X21 Protestant / X22 Roman Catholic are the congregation
# codes. On their own they miss 1,830 Colorado orgs with "CHURCH" in the name;
# the name test on its own misses 1,484 coded ones. Neither is sufficient, so
# this is the union of the two — measured, not guessed.
_NTEE_PREFIXES = ('X20', 'X21', 'X22')
_NAME_RE = re.compile(
    r'\b(CHURCH|CHAPEL|CONGREGATION|PARISH|SYNAGOGUE|MOSQUE|MINISTR\w+)\b')

# '01' is an active exemption. The rest are revoked or terminated — 39 rows of
# 36,204 in Colorado, but a revoked org is not somebody to go and see.
_ACTIVE = '01'


def _is_church(row):
    if (row.get('NTEE_CD') or '').startswith(_NTEE_PREFIXES):
        return True
    return bool(_NAME_RE.search((row.get('NAME') or '').upper()))


def _title(s):
    """BMF is all upper case; a rep reading a queue should not be shouted at."""
    out = (s or '').title()
    # Keep the small set of things Title Case gets wrong for addresses/names.
    out = re.sub(r'\b(Po|P O)\b', 'PO', out)
    out = re.sub(r'\b([NSEW])([nsew])\b', lambda m: m.group(1) + m.group(2), out)
    return out


def churches(city='', county='', state='CO', limit=None):
    """Active congregations with a street address, newest EINs last.

    Returns ``[]`` on any failure so the dispatcher falls through to
    Perplexity gap-fill, per the contract in ``sources/__init__``.
    """
    st = (state or 'CO').strip().lower()[:2]
    body = _common.fetch_cached(f'irs_eo_{st}.csv', BASE.format(state=st))
    if not body:
        return []

    rows = []
    try:
        reader = csv.DictReader(io.StringIO(body))
        for raw in reader:
            if (raw.get('STATUS') or '').strip() != _ACTIVE:
                continue
            if not _is_church(raw):
                continue
            street = _common.clean(raw.get('STREET'))
            # A PO box cannot be roofed, measured, or knocked on.
            if not street or _common.is_po_box(street):
                continue
            if city and not _common.matches_city(raw.get('CITY'), city):
                continue

            name = _title(_common.clean(raw.get('NAME')))
            ein = re.sub(r'\D', '', raw.get('EIN') or '')
            if not name or not ein:
                continue
            address = _title(street)
            rows.append({
                'company':    name,
                'first_name': '',
                'last_name':  '',
                'phone':      '',
                'email':      '',
                'website':    '',
                'address':    address,
                'city':       _title(_common.clean(raw.get('CITY'))),
                'state':      (_common.clean(raw.get('STATE')) or 'CO')[:2].upper(),
                'zip':        _common.clean(raw.get('ZIP')).split('-')[0],
                # The EIN is the whole point: a federal identifier that will
                # not drift between runs the way a model's spelling of a name
                # does. It doubles as the licence field salescrm dedupes on.
                'source_ref': f'irs_bmf:{st}:{ein}',
                'license_no': ein,
                'hook':       '',
                'icp_score':  _common.base_icp_score(address=address),
            })
    except (csv.Error, ValueError):
        return []

    if limit:
        return rows[:int(limit)]
    return rows
