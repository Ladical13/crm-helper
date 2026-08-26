"""IRS Exempt Organizations Business Master File — the free churches source.

The IRS publishes a CSV per state listing every organization it recognises as
tax-exempt, with address and NTEE code. Free, keyless, no per-lead cost.

**An honest limit, worth knowing before trusting the counts.** Churches are not
required to apply for recognition of exemption — they are exempt automatically
— so a church that never filed is simply absent from this file. The BMF is
therefore a *floor* on the churches in a city, never a census. The dispatcher
still falls through to Perplexity for whatever a segment did not fill, so the
gap is covered; it just costs money where this does not.

Fails safe: any network, HTTP or parse problem returns ``[]`` and the caller
falls through to Perplexity gap-fill, which is exactly the behaviour before
this module was wired. Wiring it can lower cost, never break a run.
"""
import csv
import io
import os

try:
    import requests
except ImportError:                            # pragma: no cover - dev only
    requests = None


# The state extract. Override if the IRS moves the file.
URL = os.environ.get(
    'IRS_BMF_CO_URL', 'https://www.irs.gov/pub/irs-soi/eo_co.csv')

# NTEE codes for congregations: X20 Christian, X21 Protestant, X22 Roman
# Catholic. Deliberately narrow — X80 (religious media) and X99 (religion,
# other) are not buildings with roofs we can bid.
NTEE_PREFIXES = ('X20', 'X21', 'X22')

TIMEOUT = 60


def _norm(s):
    return ' '.join(str(s or '').split()).strip()


def _title(s):
    """BMF names are SHOUTED. Title-case them without mangling acronyms."""
    s = _norm(s)
    return s.title() if s.isupper() else s


def churches(city='', county='', state='CO', limit=None, session=None):
    """Return congregation rows in the salescrm importer shape.

    ``county`` is accepted for interface symmetry; the BMF carries no county
    column, so filtering is by city when one is given.
    """
    if requests is None:
        return []
    try:
        sess = session or requests
        r = sess.get(URL, timeout=TIMEOUT)
        if getattr(r, 'status_code', 0) != 200:
            return []
        text = r.text
    except Exception as e:                     # pragma: no cover - network only
        print(f'[irs_bmf] fetch failed, falling through to Perplexity: {e}')
        return []

    want_city = _norm(city).lower()
    out = []
    try:
        for rec in csv.DictReader(io.StringIO(text)):
            ntee = _norm(rec.get('NTEE_CD'))
            if not ntee.startswith(NTEE_PREFIXES):
                continue
            rec_city = _norm(rec.get('CITY'))
            if want_city and rec_city.lower() != want_city:
                continue
            ein = _norm(rec.get('EIN'))
            name = _title(rec.get('NAME'))
            if not name:
                continue
            # EIN is the stable dedupe key. Most BMF rows carry no phone or
            # email at all, so without it a re-run would duplicate everything.
            if not ein:
                continue
            out.append({
                'company':    name,
                'first_name': '', 'last_name': '',
                'phone':      '', 'email':   '', 'website': '',
                'address':    _title(rec.get('STREET')),
                'city':       _title(rec_city),
                'state':      (_norm(rec.get('STATE')) or state or 'CO')[:2].upper(),
                'zip':        _norm(rec.get('ZIP')).split('-')[0],
                'license_no': '',
                'source_ref': f'irs:bmf:{ein}',
                'icp_score':  _score(rec),
            })
            if limit and len(out) >= int(limit):
                break
    except Exception as e:                     # pragma: no cover - malformed csv
        print(f'[irs_bmf] parse failed after {len(out)} rows: {e}')
        return out

    out.sort(key=lambda r: -r['icp_score'])
    return out


def _score(rec):
    """0-6, same spirit as prospector.normalize.score: reachability first.

    A congregation with a street address can be visited; asset size is a rough
    proxy for whether it owns its building, which is who can authorise a roof.
    """
    n = 0
    if _norm(rec.get('STREET')):
        n += 2
    if _norm(rec.get('CITY')):
        n += 1
    try:
        # ASSET_AMT is dollars. An organization holding real property is the
        # one that can say yes to a re-roof; a storefront congregation rents.
        if float(rec.get('ASSET_AMT') or 0) >= 250_000:
            n += 2
        elif float(rec.get('ASSET_AMT') or 0) > 0:
            n += 1
    except (TypeError, ValueError):
        pass
    if _norm(rec.get('ZIP')):
        n += 1
    return min(n, 6)
