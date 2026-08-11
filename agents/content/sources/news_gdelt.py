"""GDELT DOC 2.0 — free Colorado news signal. No API key, no account, no quota.

The cheapest useful source we have, and the only one that lets marketing cite a
**real storm**. The honesty rules forbid inventing weather events; without a
news feed the only safe move was to never mention one. With this, a hail event
carries a dated article URL and can be talked about.

Read-only GETs against a public endpoint. GDELT asks for no credential and
sets no quota, but it is a free public service — the same politeness that
applies to our own site applies here, so requests are bounded and timed out.
"""
import re
import time
from datetime import datetime

try:
    import requests
except ImportError:                            # pragma: no cover - dev only
    requests = None

API = 'https://api.gdeltproject.org/api/v2/doc/doc'
USER_AGENT = ('ProjectOneNimbus/1.0 (+https://projectoneroofingcolorado.com; '
              'contact luke@projectoneroofing.com)')
TIMEOUT = 20
RETRY_BACKOFF_S = 5

# Phrases, not bare words. The first live run matched Broncos previews and a
# Chicago baseball column, because "Denver"/"Colorado" hits sports coverage and
# bare "roof" hits stadium roofs. Requiring a two-word domain phrase costs a
# little recall and removes most of the noise.
KEYWORDS = [
    'hail damage', 'storm damage', 'roof damage', 'hail storm',
]

# GDELT has no state filter, so the state name goes in the query itself. Terms
# are OR-ed and AND-ed with Colorado, which keeps Texas hail out of a Colorado
# report — the same brand operates there, and mixing the two would be worse
# than returning nothing.
#
# Kept deliberately short: GDELT rejects a long query outright with "Your
# query was too short or too long", and the full city list plus a full
# keyword list blew straight past it. "Colorado" catches most in-state
# coverage on its own, so only the two largest markets are named separately.
_LOCALITY = 'Colorado OR Denver OR "Fort Collins"'

# Colorado's sports teams dominate any query mentioning the state. Three
# exclusions remove most of it; more would cost us the length budget.
_EXCLUDE = ['Broncos', 'Rockies', 'Nuggets']

# GDELT's limit is not published precisely. 250 is comfortably inside where it
# started rejecting ours, and the guard below trims rather than 400s.
MAX_QUERY_CHARS = 250


def available():
    """No credential needed — only the network and the `requests` package."""
    return requests is not None


def _build_query(keywords=None, locality=None, exclude=None):
    """Assemble the query, dropping keywords until it fits GDELT's limit.

    Trimming beats being rejected: a slightly narrower query returns articles,
    an over-long one returns an HTML error page and the week loses the source
    entirely. Keywords are ordered most-valuable-first for exactly this.
    """
    kws = list(keywords or KEYWORDS)
    negations = ' '.join(f'-{t}' for t in (_EXCLUDE if exclude is None else exclude))
    loc = locality or _LOCALITY

    while kws:
        terms = ' OR '.join(f'"{k}"' if ' ' in k else k for k in kws)
        q = f'({terms}) AND ({loc}) sourcelang:english {negations}'.strip()
        if len(q) <= MAX_QUERY_CHARS or len(kws) == 1:
            return q
        kws.pop()      # drop the least important keyword and retry
    return f'("hail damage") AND ({loc}) sourcelang:english'


# GDELT matches the whole article body, so a national insurance roundup that
# lists every state matches "Colorado" and arrives looking local. A live run
# returned Arkansas homeowners, Kansas City, Georgia and a Writer's Digest
# piece on morally grey characters. Two cheap signals separate real local
# coverage from that: the headline names a Colorado place, or the outlet is a
# Colorado one.
_CO_PLACES = (
    'colorado', 'denver', 'fort collins', 'greeley', 'loveland', 'boulder',
    'aurora', 'longmont', 'windsor', 'pueblo', 'front range', 'larimer',
    'weld county', 'castle rock', 'lakewood', 'thornton', 'westminster',
)
_CO_DOMAINS = (
    'colorado', 'denver', 'coloradoan', 'gazette.com', '9news', 'cpr.org',
    'summitdaily', 'aspentimes', 'steamboatpilot', 'greeleytribune',
    'reporterherald', 'timescall', 'dailycamera', 'westword', 'krdo',
    'kktv', 'fox21news', 'kdvr', 'thedenverchannel', 'coloradopolitics',
    'canoncitydailyrecord', 'ourcoloradonews',
)


# Places that contain "Colorado" and are not this Colorado. The live feed
# returned "Storm damage leaves Colorado City residents..." from a station in
# Abilene — Colorado City is in TEXAS, which is the sibling franchise's patch
# and the exact confusion this whole profile is meant to prevent.
#
# Western Colorado is real Colorado but outside our service area: the profile
# says east of the mountains, so a Western Slope mudslide is not our story.
_CO_FALSE_FRIENDS = (
    'colorado city',        # Texas (and Arizona)
    'colorado river',       # mostly Arizona / California / Texas
    'western colorado', 'western slope', 'grand junction', 'durango',
)


def looks_colorado(article):
    """True when this is plausibly *our* Colorado rather than a mention."""
    title = (article.get('title') or '').lower()
    domain = (article.get('domain') or '').lower()
    if any(f in title for f in _CO_FALSE_FRIENDS):
        return False
    if any(p in title for p in _CO_PLACES):
        return True
    return any(d in domain for d in _CO_DOMAINS)


def _title_key(title):
    """Normalise a headline so syndicated copies collapse to one.

    Outlets truncate and re-punctuate the same wire story, so compare on the
    first few alphanumeric words rather than the whole string.
    """
    words = re.findall(r'[a-z0-9]+', (title or '').lower())
    return ' '.join(words[:8])


def _parse_seendate(raw):
    """GDELT stamps are `20260810T143000Z`. Return ISO, or '' if unparseable."""
    try:
        return datetime.strptime(raw, '%Y%m%dT%H%M%SZ').strftime('%Y-%m-%dT%H:%M:%SZ')
    except (ValueError, TypeError):
        return ''


def pull(days=7, limit=40, keywords=None):
    """Recent Colorado articles matching the keywords.

    Returns ``{articles, note, available}``. Never raises — a dead feed is one
    missing input, not a failed run.
    """
    if not available():
        return {'articles': [], 'available': False,
                'note': 'the `requests` package is not installed'}
    params = {
        'query': _build_query(keywords),
        'mode': 'artlist',
        'format': 'json',
        'maxrecords': min(int(limit), 250),
        'timespan': f'{max(1, int(days))}d',
        'sort': 'datedesc',
    }
    # GDELT rate-limits a free public endpoint aggressively and transiently —
    # a 429 on the first call and a clean 200 five seconds later is normal, so
    # one backoff is worth it before writing the week off.
    r = None
    for attempt in range(2):
        try:
            r = requests.get(API, params=params, timeout=TIMEOUT,
                             headers={'User-Agent': USER_AGENT})
        except Exception as e:                                   # noqa: BLE001
            return {'articles': [], 'available': True,
                    'note': f'GDELT unreachable ({type(e).__name__})'}
        if r.status_code != 429:
            break
        if attempt == 0:
            time.sleep(RETRY_BACKOFF_S)
    if r.status_code == 429:
        return {'articles': [], 'available': True,
                'note': 'GDELT rate-limited us (HTTP 429) — transient, try again '
                        'shortly. This costs the run one input, not the run.'}
    if not r.ok:
        return {'articles': [], 'available': True,
                'note': f'GDELT returned HTTP {r.status_code}'}

    # GDELT answers a malformed query with an HTML error page and HTTP 200,
    # so a JSON parse failure here means a bad query, not a dead service.
    try:
        body = r.json()
    except ValueError:
        snippet = re.sub(r'<[^>]+>', ' ', (r.text or ''))[:120].strip()
        return {'articles': [], 'available': True,
                'note': f'GDELT did not return JSON — likely a query error: {snippet}'}

    # Dedupe by URL *and* by normalised title. A single press release gets
    # syndicated across dozens of near-identical outlets — the first live call
    # returned one competitor's release six times from six domains. Counting
    # that as six signals would let anyone with a wire budget set our content
    # agenda. The syndication count is kept, because it is mildly informative
    # on its own.
    seen_urls, by_title, articles = set(), {}, []
    for a in (body.get('articles') or []):
        url = (a.get('url') or '').strip()
        title = (a.get('title') or '').strip()
        if not url or not title or url in seen_urls:
            continue
        seen_urls.add(url)
        key = _title_key(title)
        if key in by_title:
            by_title[key]['syndicated_copies'] += 1
            continue
        record = {
            'title': title,
            'url': url,
            'domain': (a.get('domain') or '').strip(),
            'seen_at': _parse_seendate(a.get('seendate')),
            'syndicated_copies': 1,
            'source': 'gdelt',
        }
        by_title[key] = record
        articles.append(record)

    # Drop mentions-of-Colorado that are not Colorado stories, and say how
    # many went — a filter that silently halves the feed is a filter nobody
    # can debug.
    kept = [a for a in articles if looks_colorado(a)]
    filtered = len(articles) - len(kept)
    articles = kept
    note = (f'{len(articles)} Colorado article(s) in the last {days} days'
            if articles else f'no Colorado matches in the last {days} days')
    if filtered:
        note += f' ({filtered} out-of-state mention(s) filtered)'
    return {'articles': articles, 'available': True, 'note': note,
            'filtered_out': filtered}


def as_topics(result):
    """Reshape articles into the topic dicts the scorer consumes.

    One topic per article rather than per cluster: GDELT titles are already
    headline-shaped, and clustering them here would duplicate the merge step
    that ``score.merge_sources`` does across every source.
    """
    topics = []
    for a in (result.get('articles') or []):
        topics.append({
            'topic': a['title'],
            'summary': f'Reported by {a["domain"]}'
                       + (f' on {a["seen_at"][:10]}' if a.get('seen_at') else ''),
            'citations': [a['url']],
            'source_names': ['gdelt'],
        })
    return topics


def storm_events(days=30, limit=25):
    """Hail and storm coverage only — what a restoration pitch may reference.

    Narrower than ``pull`` on purpose. A generic roofing article is content
    fodder; a dated hail report is the thing a rep can point at, and the thing
    the honesty rules otherwise forbid asserting.
    """
    return pull(days=days, limit=limit,
                keywords=['hail damage', 'hail storm', 'severe storm'])
