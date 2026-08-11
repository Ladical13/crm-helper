"""Bing Webmaster Tools — the only *measured* search data we can own.

Search Console is owner-gated at the franchise. Bing is not: verification is
independent of Google, so a meta tag in the CMS makes the property ours
outright. It is a smaller slice of search than Google, and it is real.

That distinction runs through everything here. Bing numbers are **measured**,
so unlike the rest of the strategist they may be stated as fact — but always
labelled as Bing, never as "search". A reader who takes a Bing impression
count for a Google one has been misled just as surely as by an invented
number, so every figure that leaves this module carries its source.

Needs ``BING_WEBMASTER_API_KEY`` and ``BING_SITE_URL``. Read-only GETs.
"""
import os
from urllib.parse import quote

try:
    import requests
except ImportError:                            # pragma: no cover - dev only
    requests = None

API_ROOT = 'https://ssl.bing.com/webmaster/api.svc/json'
TIMEOUT = 20

# Every figure this module emits is tagged with this, so no downstream caller
# can render it as generic "search data".
SOURCE_LABEL = 'Bing Webmaster Tools'


class BingUnavailable(RuntimeError):
    """Not configured, or Bing refused the call."""


def available():
    return bool(os.environ.get('BING_WEBMASTER_API_KEY', '').strip()
                and os.environ.get('BING_SITE_URL', '').strip()
                and requests is not None)


def _call(method, **params):
    key = os.environ.get('BING_WEBMASTER_API_KEY', '').strip()
    site = os.environ.get('BING_SITE_URL', '').strip()
    if not (key and site):
        raise BingUnavailable('BING_WEBMASTER_API_KEY / BING_SITE_URL are not set')
    if requests is None:
        raise BingUnavailable('the `requests` package is not installed')
    query = {'apikey': key, 'siteUrl': site}
    query.update(params)
    url = f'{API_ROOT}/{method}?' + '&'.join(
        f'{k}={quote(str(v), safe="")}' for k, v in query.items())
    try:
        r = requests.get(url, timeout=TIMEOUT,
                         headers={'Accept': 'application/json'})
    except Exception as e:                                       # noqa: BLE001
        raise BingUnavailable(f'Bing unreachable ({type(e).__name__})') from e
    if r.status_code in (401, 403):
        raise BingUnavailable(
            f'HTTP {r.status_code} — the API key was rejected, or this account '
            f'is not verified for {site}')
    if not r.ok:
        raise BingUnavailable(f'Bing returned HTTP {r.status_code}')
    try:
        body = r.json()
    except ValueError:
        raise BingUnavailable('Bing did not return JSON')
    return body.get('d', body)


def query_stats(limit=25):
    """Top search queries with impressions, clicks and average position.

    Every row is measured — this is the one place in the strategist that may
    state a number, and every number says "Bing" beside it.
    """
    rows = _call('GetQueryStats') or []
    out = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        out.append({
            'query': row.get('Query', ''),
            'impressions': int(row.get('Impressions') or 0),
            'clicks': int(row.get('Clicks') or 0),
            'avg_position': round(float(row.get('AvgImpressionPosition') or 0), 1),
            'source': SOURCE_LABEL,
        })
    out.sort(key=lambda r: -r['impressions'])
    return out[:limit]


def page_stats(limit=25):
    """Per-page impressions and clicks."""
    rows = _call('GetPageStats') or []
    out = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        out.append({
            'url': row.get('Query', '') or row.get('Url', ''),
            'impressions': int(row.get('Impressions') or 0),
            'clicks': int(row.get('Clicks') or 0),
            'source': SOURCE_LABEL,
        })
    out.sort(key=lambda r: -r['impressions'])
    return out[:limit]


def summary():
    """Everything the weekly report needs, or a reason it has nothing.

    Never raises: an unconfigured or unhappy Bing costs the report one
    section, not the run.
    """
    if not available():
        return {'available': False, 'queries': [], 'pages': [],
                'note': 'BING_WEBMASTER_API_KEY / BING_SITE_URL are not set'}
    try:
        queries = query_stats()
    except BingUnavailable as e:
        return {'available': False, 'queries': [], 'pages': [], 'note': str(e)}
    try:
        pages = page_stats()
    except BingUnavailable:
        pages = []
    return {
        'available': True,
        'queries': queries,
        'pages': pages,
        'source': SOURCE_LABEL,
        'note': f'{len(queries)} query row(s), {len(pages)} page row(s)',
    }


def winners_and_decliners(queries, top=5):
    """Split by clicks. **Not** a period comparison — Bing's basic API returns
    a current window, not a delta, so this is "what earns clicks" versus "what
    is seen and not clicked". Labelled honestly rather than dressed up as
    trend data we do not have.
    """
    with_impressions = [q for q in queries if q['impressions'] > 0]
    earning = sorted((q for q in with_impressions if q['clicks'] > 0),
                     key=lambda q: -q['clicks'])[:top]
    seen_not_clicked = sorted((q for q in with_impressions if q['clicks'] == 0),
                              key=lambda q: -q['impressions'])[:top]
    return {'earning_clicks': earning, 'seen_not_clicked': seen_not_clicked}
