"""Polite, read-only crawl of our own public website.

Hard limits, all enforced here rather than trusted to callers:

  * **robots.txt is obeyed**, checked against our own user-agent. A disallowed
    URL is skipped, not fetched-and-discarded.
  * **A clear, identifying user-agent** with a contact URL, so anyone reading
    their access log can tell who we are and how to reach us.
  * **GET only.** No POST, no form submission, no query strings we invented.
    A test greps this package for write verbs.
  * **Bounded**: max pages, max depth, per-request timeout, and a delay
    between requests.
  * **Same origin only.** Competitor pages are researched via Perplexity, not
    crawled — crawling someone else's site to grade it is a different thing
    with different manners.

Results are cached in ``seo_page_cache`` so a re-run inside the TTL costs no
requests at all.
"""
import time
import urllib.robotparser
from urllib.parse import urljoin, urlparse, urldefrag

try:
    import requests
except ImportError:                            # pragma: no cover - dev only
    requests = None

from .. import config
from . import inspect as page_inspect

USER_AGENT = ('ProjectOneNimbus/1.0 (+https://projectoneroofing.com; '
              'internal SEO audit; contact luke@projectoneroofing.com)')

DEFAULT_MAX_PAGES = 40
DEFAULT_MAX_DEPTH = 3
REQUEST_TIMEOUT   = 15
CRAWL_DELAY       = 1.0      # seconds between requests, floor
CACHE_TTL_HOURS   = 24

_SKIP_EXTENSIONS = ('.pdf', '.jpg', '.jpeg', '.png', '.gif', '.svg', '.webp',
                    '.zip', '.mp4', '.mov', '.css', '.js', '.ico', '.xml')


class CrawlError(RuntimeError):
    """The crawl could not start — bad base URL, no network, robots unreachable."""


def _norm(url):
    """Drop the fragment and any trailing slash so one page is one cache key."""
    url, _frag = urldefrag(url or '')
    if url.endswith('/') and urlparse(url).path != '/':
        url = url[:-1]
    return url


def _same_origin(url, base):
    a, b = urlparse(url), urlparse(base)
    return (a.scheme, a.netloc) == (b.scheme, b.netloc)


def _crawlable(url):
    path = urlparse(url).path.lower()
    return not path.endswith(_SKIP_EXTENSIONS)


def robots_for(base_url):
    """Load robots.txt. Returns (parser, note).

    A robots.txt we cannot read is treated as **allow**, matching the RFC and
    every mainstream crawler — but the note travels into the report so the
    decision is visible rather than silent.
    """
    rp = urllib.robotparser.RobotFileParser()
    robots_url = urljoin(base_url, '/robots.txt')
    rp.set_url(robots_url)
    if requests is None:
        return rp, 'requests unavailable — robots.txt not checked'
    try:
        r = requests.get(robots_url, headers={'User-Agent': USER_AGENT},
                         timeout=REQUEST_TIMEOUT)
    except Exception as e:                                       # noqa: BLE001
        return rp, f'robots.txt unreachable ({type(e).__name__}) — proceeding as allowed'
    if r.status_code == 404:
        return rp, 'no robots.txt — proceeding as allowed'
    if not r.ok:
        return rp, f'robots.txt returned HTTP {r.status_code} — proceeding as allowed'
    rp.parse(r.text.splitlines())
    return rp, 'robots.txt honoured'


def sitemap_urls(base_url, limit=200):
    """Read /sitemap.xml. Returns (urls, note).

    Parsed with a string scan rather than an XML parser on purpose: sitemaps
    in the wild are frequently malformed, and a hard parse failure would cost
    us the whole crawl seed for a stray ampersand.
    """
    if requests is None:
        return [], 'requests unavailable'
    url = urljoin(base_url, '/sitemap.xml')
    try:
        r = requests.get(url, headers={'User-Agent': USER_AGENT},
                         timeout=REQUEST_TIMEOUT)
    except Exception as e:                                       # noqa: BLE001
        return [], f'sitemap unreachable ({type(e).__name__})'
    if not r.ok:
        return [], f'sitemap returned HTTP {r.status_code}'
    body = r.text or ''
    urls, cursor = [], 0
    while len(urls) < limit:
        start = body.find('<loc>', cursor)
        if start < 0:
            break
        end = body.find('</loc>', start)
        if end < 0:
            break
        loc = body[start + 5:end].strip()
        cursor = end + 6
        if loc and _same_origin(loc, base_url) and _crawlable(loc):
            urls.append(_norm(loc))
    # A sitemap index points at more sitemaps rather than pages; say so instead
    # of returning an empty list that reads like an empty site.
    if not urls and '<sitemapindex' in body:
        return [], 'sitemap is an index of sitemaps — nested sitemaps not followed in v1'
    return urls, f'{len(urls)} URLs in sitemap'


def _cached(url, ttl_hours):
    with config.get_cache_db() as db:
        row = db.execute('SELECT fetched_at, status_code, extracted, error '
                         'FROM seo_page_cache WHERE url = ?', (url,)).fetchone()
    if not row:
        return None
    import json
    from datetime import datetime, timedelta
    try:
        fetched = datetime.strptime(row['fetched_at'], '%Y-%m-%dT%H:%M:%SZ')
    except ValueError:
        return None
    if datetime.utcnow() - fetched > timedelta(hours=ttl_hours):
        return None
    try:
        data = json.loads(row['extracted'] or '{}')
    except ValueError:
        return None
    data['url'] = url
    data['cached'] = True
    data['status_code'] = row['status_code']
    data['error'] = row['error']
    return data


def _store(url, page):
    import json
    payload = {k: v for k, v in page.items()
               if k not in ('cached', 'url', 'status_code', 'error')}
    with config.get_cache_db() as db:
        db.execute(
            'INSERT OR REPLACE INTO seo_page_cache '
            '(url, fetched_at, status_code, extracted, error) VALUES (?, ?, ?, ?, ?)',
            (url, config.now_iso(), int(page.get('status_code') or 0),
             json.dumps(payload), page.get('error', '')))
        db.commit()


def fetch_page(url, timeout=REQUEST_TIMEOUT):
    """One read-only GET, parsed into metadata. Never raises."""
    if requests is None:
        return {'url': url, 'status_code': 0, 'error': 'requests unavailable',
                'cached': False}
    try:
        r = requests.get(url, headers={'User-Agent': USER_AGENT},
                         timeout=timeout, allow_redirects=True)
    except Exception as e:                                       # noqa: BLE001
        return {'url': url, 'status_code': 0, 'cached': False,
                'error': f'{type(e).__name__}: {e}'}
    if not r.ok:
        return {'url': url, 'status_code': r.status_code, 'cached': False,
                'error': f'HTTP {r.status_code}'}
    ctype = (r.headers.get('Content-Type') or '').lower()
    if 'html' not in ctype:
        return {'url': url, 'status_code': r.status_code, 'cached': False,
                'error': f'not HTML ({ctype.split(";")[0] or "unknown"})'}
    page = page_inspect.extract(r.text or '', url)
    page.update({'url': url, 'status_code': r.status_code,
                 'cached': False, 'error': ''})
    return page


def crawl_site(base_url, max_pages=DEFAULT_MAX_PAGES, max_depth=DEFAULT_MAX_DEPTH,
               use_cache=True, ttl_hours=CACHE_TTL_HOURS, delay=None,
               write_cache=True):
    """Crawl our own site. Returns ``{pages, notes, skipped, robots_note}``.

    Seeded from the sitemap when there is one, falling back to a link crawl
    from the homepage. Never raises for a single bad page — a page that fails
    is recorded with its error and the crawl continues.

    ``delay`` defaults to ``CRAWL_DELAY`` read **at call time**, not bound as a
    default argument. A default argument would freeze the value at import and
    silently ignore anyone setting the module constant — which is exactly what
    a caller lowering it for a test, or raising it to be gentler on a slow
    host, would expect to work.
    """
    delay = CRAWL_DELAY if delay is None else delay
    base_url = (base_url or '').rstrip('/')
    if not base_url.startswith('http'):
        raise CrawlError(f'base URL must be absolute http(s): {base_url!r}')

    rp, robots_note = robots_for(base_url)
    seeds, sitemap_note = sitemap_urls(base_url)

    def allowed(u):
        try:
            return rp.can_fetch(USER_AGENT, u)
        except Exception:                                        # noqa: BLE001
            return True     # an unparseable rule must not silently block us

    queue = [(u, 1) for u in seeds[:max_pages]] or [(_norm(base_url), 0)]
    seen, pages, skipped = set(), [], []
    last_fetch = 0.0

    while queue and len(pages) < max_pages:
        url, depth = queue.pop(0)
        url = _norm(url)
        if url in seen or depth > max_depth:
            continue
        seen.add(url)
        if not _same_origin(url, base_url) or not _crawlable(url):
            continue
        if not allowed(url):
            skipped.append({'url': url, 'reason': 'disallowed by robots.txt'})
            continue

        page = _cached(url, ttl_hours) if use_cache else None
        if page is None:
            # Politeness delay applies to live requests only — a cache hit
            # costs the site nothing, so there is nothing to be polite about.
            wait = delay - (time.time() - last_fetch)
            if wait > 0:
                time.sleep(wait)
            page = fetch_page(url)
            last_fetch = time.time()
            if write_cache:
                _store(url, page)

        page['depth'] = depth
        pages.append(page)

        for link in (page.get('internal_links') or []):
            target = _norm(urljoin(url, link))
            if target not in seen and _same_origin(target, base_url):
                queue.append((target, depth + 1))

    return {
        'pages': pages,
        'skipped': skipped,
        'robots_note': robots_note,
        'sitemap_note': sitemap_note,
        'base_url': base_url,
        'user_agent': USER_AGENT,
        'limits': {'max_pages': max_pages, 'max_depth': max_depth,
                   'timeout_s': REQUEST_TIMEOUT, 'delay_s': delay},
    }
