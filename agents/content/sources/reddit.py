"""Reddit listening — the best source of real customer language we can get.

Perplexity tells us what it thinks homeowners ask. Reddit shows us homeowners
asking, in their own words, with a citable permalink. That is a primary source
where the rest of our research is secondary, and it is free.

Uses Reddit's **application-only OAuth** over plain ``requests`` rather than
PRAW. The whole interaction is one token call and a GET per subreddit, which
does not justify a dependency in a repo that vendored Leaflet specifically to
avoid one.

Needs ``REDDIT_CLIENT_ID`` and ``REDDIT_CLIENT_SECRET`` from a Reddit app
(type: **script**). Without them every call returns empty and says so — the
weekly run loses one input, not the run.

**Never reproduce a Redditor's words in published copy.** Their post is theirs,
and a homeowner finding their own complaint quoted in a roofing advert is a
worse outcome than any amount of good targeting is worth. Use these to learn
what to write about, then write it ourselves. ``brief.py`` carries that rule
into every brief built from a Reddit finding.
"""
import os
import re
import time

try:
    import requests
except ImportError:                            # pragma: no cover - dev only
    requests = None

TOKEN_URL = 'https://www.reddit.com/api/v1/access_token'
API_ROOT  = 'https://oauth.reddit.com'
TIMEOUT   = 20
PER_REQUEST_DELAY = 1.0     # Reddit allows ~100 QPM; nowhere near it, but be polite

TARGET_SUBS = [
    'HomeImprovement', 'Roofing',
    'Denver', 'coloradosprings', 'FortCollins', 'Colorado',
    'Insurance', 'HOA', 'RealEstate',
    'homeowners',
]

# Colorado subs are worth more than national ones for a local contractor — a
# hail question in r/Denver is a prospect, the same question in r/Roofing is
# somebody in Florida.
LOCAL_SUBS = {'denver', 'coloradosprings', 'fortcollins', 'colorado'}

KEYWORDS = [
    'roof', 'roofing', 'roofer', 'shingle', 'hail', 'storm', 'leak',
    'insurance claim', 'adjuster', 'depreciation', 'deductible',
    'contractor', 'quote', 'estimate', 'siding', 'gutter',
]

_QUESTION_OPENERS = re.compile(
    r'^(how|what|why|when|where|which|who|does|do|did|can|could|should|is|are|'
    r'was|were|will|would|any|anyone|has anyone|help)\b', re.IGNORECASE)

_token_cache = {'token': '', 'expires_at': 0.0}


def user_agent():
    """Reddit requires a descriptive, unique UA and throttles generic ones."""
    return os.environ.get(
        'REDDIT_USER_AGENT',
        'python:project-one-nimbus:v1.0 (internal marketing research; '
        'contact luke@projectoneroofing.com)')


def available():
    return bool(os.environ.get('REDDIT_CLIENT_ID', '').strip()
                and os.environ.get('REDDIT_CLIENT_SECRET', '').strip()
                and requests is not None)


def _access_token():
    """App-only OAuth token, cached until shortly before it expires."""
    if _token_cache['token'] and _token_cache['expires_at'] > time.time() + 60:
        return _token_cache['token']
    client_id = os.environ.get('REDDIT_CLIENT_ID', '').strip()
    secret    = os.environ.get('REDDIT_CLIENT_SECRET', '').strip()
    if not (client_id and secret):
        raise RuntimeError('REDDIT_CLIENT_ID / REDDIT_CLIENT_SECRET are not set')
    r = requests.post(TOKEN_URL, auth=(client_id, secret), timeout=TIMEOUT,
                      data={'grant_type': 'client_credentials'},
                      headers={'User-Agent': user_agent()})
    if r.status_code == 401:
        raise RuntimeError('Reddit rejected the credentials (HTTP 401) — check '
                           'the app is type "script" and the secret is current')
    if not r.ok:
        raise RuntimeError(f'Reddit token exchange failed (HTTP {r.status_code})')
    body = r.json() or {}
    token = body.get('access_token', '')
    if not token:
        raise RuntimeError('Reddit returned no access_token')
    _token_cache['token'] = token
    _token_cache['expires_at'] = time.time() + float(body.get('expires_in', 3600))
    return token


def _matches(text):
    low = (text or '').lower()
    return [k for k in KEYWORDS if k in low]


def is_question(title):
    t = (title or '').strip()
    return t.endswith('?') or bool(_QUESTION_OPENERS.match(t))


def _fetch_sub(sub, token, period='week', limit=25):
    r = requests.get(f'{API_ROOT}/r/{sub}/top',
                     params={'t': period, 'limit': min(int(limit), 100)},
                     headers={'Authorization': f'bearer {token}',
                              'User-Agent': user_agent()},
                     timeout=TIMEOUT)
    if r.status_code in (403, 404):
        return [], f'r/{sub}: not readable (HTTP {r.status_code})'
    if r.status_code == 429:
        return [], f'r/{sub}: rate limited'
    if not r.ok:
        return [], f'r/{sub}: HTTP {r.status_code}'
    try:
        children = ((r.json() or {}).get('data') or {}).get('children') or []
    except ValueError:
        return [], f'r/{sub}: response was not JSON'
    return children, ''


def pull(subs=None, period='week', limit=25):
    """Keyword-matching posts from the target subreddits.

    Returns ``{posts, note, available}``. Never raises.
    """
    if not available():
        return {'posts': [], 'available': False,
                'note': 'REDDIT_CLIENT_ID / REDDIT_CLIENT_SECRET are not set'}
    try:
        token = _access_token()
    except RuntimeError as e:
        return {'posts': [], 'available': True, 'note': str(e)}
    except Exception as e:                                       # noqa: BLE001
        return {'posts': [], 'available': True,
                'note': f'Reddit auth failed ({type(e).__name__})'}

    posts, notes = [], []
    for i, sub in enumerate(subs or TARGET_SUBS):
        if i:
            time.sleep(PER_REQUEST_DELAY)
        try:
            children, err = _fetch_sub(sub, token, period, limit)
        except Exception as e:                                   # noqa: BLE001
            notes.append(f'r/{sub}: {type(e).__name__}')
            continue
        if err:
            notes.append(err)
            continue
        for child in children:
            d = (child or {}).get('data') or {}
            if d.get('over_18'):
                continue
            title = (d.get('title') or '').strip()
            body  = (d.get('selftext') or '').strip()
            hits = _matches(f'{title} {body}')
            if not hits:
                continue
            permalink = d.get('permalink') or ''
            posts.append({
                'title': title,
                # A short excerpt for context only. Never publish it — see the
                # module docstring.
                'excerpt': body[:280],
                'url': f'https://www.reddit.com{permalink}' if permalink else '',
                'subreddit': d.get('subreddit', sub),
                'is_local': str(d.get('subreddit', sub)).lower() in LOCAL_SUBS,
                'is_question': is_question(title),
                'comments': int(d.get('num_comments') or 0),
                'matched': hits,
                'source': 'reddit',
            })

    note = f'{len(posts)} matching post(s) across {len(subs or TARGET_SUBS)} subreddits'
    if notes:
        note += ' — ' + '; '.join(notes[:3])
    return {'posts': posts, 'available': True, 'note': note}


def questions(result, local_only=False, limit=20):
    """Just the posts phrased as questions — the highest-value subset.

    A question title *is* the customer's own phrasing of their problem, which
    is exactly what a FAQ or service page needs to answer.
    """
    posts = [p for p in (result.get('posts') or []) if p['is_question']]
    if local_only:
        posts = [p for p in posts if p['is_local']]
    # Local first, then by discussion volume — a thread people replied to is a
    # question more than one person had.
    posts.sort(key=lambda p: (not p['is_local'], -p['comments']))
    return posts[:limit]


def as_topics(result):
    """Reshape into the topic dicts the scorer consumes."""
    topics = []
    for p in (result.get('posts') or []):
        where = f'r/{p["subreddit"]}'
        topics.append({
            'topic': p['title'],
            'summary': (f'Asked in {where}'
                        + (f', {p["comments"]} comments' if p['comments'] else '')
                        + ('. Colorado-local subreddit.' if p['is_local'] else '.')),
            'citations': [p['url']] if p['url'] else [],
            'source_names': ['reddit'],
            'is_local': p['is_local'],
        })
    return topics
