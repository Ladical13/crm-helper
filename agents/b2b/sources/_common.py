"""Shared plumbing for the free open-data sources.

Two things every source here needs and none of them should re-derive: a cached
fetch of a large public dataset, and the reachability score the dispatcher
sorts on before it pays to enrich anything.
"""
import json
import os
import re
import time

try:
    import requests
except ImportError:                            # pragma: no cover - dev only
    requests = None

from ... import config

USER_AGENT = ('ProjectOneNimbus/1.0 (+https://projectoneroofingcolorado.com; '
              'contact luke@projectoneroofing.com)')
TIMEOUT = 180

# The model is told not to fabricate contact details and complies by writing
# "unknown" into the field. Open data has its own dialect of the same thing.
_PLACEHOLDERS = {
    'unknown', 'n/a', 'na', 'none', 'null', 'not available', 'not listed',
    'unlisted', 'not found', 'not provided', 'no phone', 'no email', 'tbd',
    '-', 'no data', 'missing', '.',
}

_PO_BOX = re.compile(r'^\s*(p\.?\s*o\.?\s*box|post office box|pob\b)', re.I)


def clean(s):
    """Strip, and treat a stated non-answer as the empty string it means."""
    if not isinstance(s, str):
        return '' if s is None else str(s).strip()
    s = s.strip()
    return '' if s.lower().strip('.') in _PLACEHOLDERS else s


def is_po_box(street):
    """A PO box is not a roof. It is a fine mailing address and a useless
    lead: a rep cannot look at it, measure it, or knock on it."""
    return bool(_PO_BOX.match(street or ''))


def base_icp_score(phone='', email='', website='', address=''):
    """What can be ranked BEFORE spending money to enrich anything.

    The dispatcher sorts on ``icp_score`` and enriches the top N, so this
    decides who is worth paying for. Reachability is the honest signal at this
    stage: a partner nobody can call, mail or visit is not workable however
    good a fit they look. ``enrich.py`` adds storm (+2) and decision-maker
    (+1) nudges on top of whatever lands here.
    """
    return ((2 if clean(phone) else 0)
            + (2 if clean(email) else 0)
            + (1 if clean(website) else 0)
            + (1 if clean(address) else 0))


def _cache_dir():
    d = os.path.join(config.data_dir(), 'opendata')
    os.makedirs(d, exist_ok=True)
    return d


def fetch_cached(name, url, *, ttl_days=30, params=None, binary=False):
    """Fetch a public dataset, cached on the volume by ``name``.

    Returns the body (str, or bytes when ``binary``) or ``None``. Never
    raises: every caller's contract with the dispatcher is that a dead source
    returns no rows and the run falls through to Perplexity, so a download
    failure must cost one input rather than the run.

    These files are tens of megabytes and change monthly at most, so re-pulling
    one per rep per city would be both slow and rude to the publisher.
    """
    path = os.path.join(_cache_dir(), name)
    if os.path.exists(path):
        age_days = (time.time() - os.path.getmtime(path)) / 86400.0
        if age_days < ttl_days:
            try:
                with open(path, 'rb' if binary else 'r',
                          **({} if binary else {'encoding': 'utf-8'})) as f:
                    return f.read()
            except OSError:
                pass                       # fall through and re-download

    if requests is None:
        return None
    try:
        r = requests.get(url, params=params, timeout=TIMEOUT,
                         headers={'User-Agent': USER_AGENT})
        if not r.ok:
            return None
        body = r.content if binary else r.content.decode('utf-8', 'replace')
    except Exception:                                            # noqa: BLE001
        return None

    # Write via a temp file so an interrupted download never leaves a
    # half-written dataset that looks fresh for the next 30 days.
    tmp = path + '.tmp'
    try:
        with open(tmp, 'wb' if binary else 'w',
                  **({} if binary else {'encoding': 'utf-8'})) as f:
            f.write(body)
        os.replace(tmp, path)
    except OSError:
        try:
            os.remove(tmp)
        except OSError:
            pass
    return body


def fetch_cached_path(name, url, *, ttl_days=30, params=None):
    """Same contract as ``fetch_cached``, but returns the cached file's PATH.

    The county assessor extracts are 30–50 MB each. Handing back a string
    means holding the whole file in memory and then a parsed copy beside it,
    inside a container that also runs two gunicorn workers. Callers stream
    from the path instead and never materialise more than one row.

    Returns ``None`` if the file could not be fetched.
    """
    path = os.path.join(_cache_dir(), name)
    if os.path.exists(path):
        age_days = (time.time() - os.path.getmtime(path)) / 86400.0
        if age_days < ttl_days and os.path.getsize(path) > 0:
            return path

    if requests is None:
        return None
    tmp = path + '.tmp'
    try:
        with requests.get(url, params=params, timeout=TIMEOUT, stream=True,
                          headers={'User-Agent': USER_AGENT}) as r:
            if not r.ok:
                return None
            with open(tmp, 'wb') as f:
                for chunk in r.iter_content(chunk_size=1 << 20):
                    if chunk:
                        f.write(chunk)
        os.replace(tmp, path)
        return path
    except Exception:                                            # noqa: BLE001
        try:
            os.remove(tmp)
        except OSError:
            pass
        # A stale copy still beats no data at all — a month-old owner name is
        # not wrong, just old, and the alternative is falling through to a
        # model guessing at who owns a building.
        return path if os.path.exists(path) else None


def title(s):
    """Open data shouts. IRS BMF and NCES CCD are both all upper case, and a
    rep reading a queue of SCREAMING NAMES reads it as machine output.

    ``str.title()`` alone is not enough: it renders "PO BOX" as "Po Box",
    "18TH AVE" as "18Th Ave", and the "NE"/"SW" directionals as "Ne"/"Sw".

    Text that is already mixed case is left exactly as it is. NCES CCD names
    its schools properly, and re-casing them would turn "McKinley Elementary"
    into "Mckinley Elementary" — breaking correct data to fix a problem it
    does not have. Only shouting gets quietened.
    """
    s = (s or '').strip()
    if any(c.islower() for c in s):
        return s
    out = s.title()
    # These run against already-title-cased text, so they must be case
    # insensitive: the thing being corrected is "Po", not "PO".
    out = re.sub(r'\bP\s?O\b', 'PO', out, flags=re.I)
    out = re.sub(r'\b(\d+)(St|Nd|Rd|Th)\b',
                 lambda m: m.group(1) + m.group(2).lower(), out, flags=re.I)
    out = re.sub(r'\b([NS][EW])\b', lambda m: m.group(1).upper(), out,
                 flags=re.I)
    return out


def clip(s, limit=180):
    """Trim to ``limit`` on a word boundary.

    A hard slice once ended a customer-facing answer at "as part of t". The
    same rule applies to anything a rep reads off a card.
    """
    s = (s or '').strip()
    if len(s) <= limit:
        return s
    cut = s[:limit]
    space = cut.rfind(' ')
    if space > limit * 0.6:
        cut = cut[:space]
    return cut.rstrip(' ,;·-') + '…'


def matches_city(row_city, wanted):
    """Open data abbreviates. 'COLORADO SPGS' is Colorado Springs, and a rep
    filtering on the real name would otherwise see none of them."""
    a = re.sub(r'[^a-z]', '', (row_city or '').lower())
    b = re.sub(r'[^a-z]', '', (wanted or '').lower())
    if not b:
        return True
    if a == b:
        return True
    # 'coloradospgs' vs 'coloradosprings' — one is a prefix-ish abbreviation.
    short, long_ = sorted((a, b), key=len)
    return len(short) >= 6 and long_.startswith(short[:6]) and short in long_ or \
        (len(short) >= 8 and long_[:8] == short[:8])
