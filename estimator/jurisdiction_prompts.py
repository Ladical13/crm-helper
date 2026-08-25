"""Perplexity prompt template + citation allowlist for jurisdiction code lookup.

Kept in its own file so the exact prompt is easy to audit, iterate on, and
test in isolation. The rules encoded here are what stop model hallucination
from reaching a customer:

  * The schema is fixed. Missing values become the literal string "unknown"
    (matches `agents.perplexity.search_json`'s system-append), never a guess.
  * At least one citation returned by Perplexity must resolve to a host on
    ALLOWED_CITATION_HOSTS, or to one of THIS jurisdiction's own domains
    (`jurisdiction_hosts()`). A city building department page, the published
    municipal code (Municode / American Legal / eCode360 / Sterling / Colorado
    Code Publishing), or the ICC code adoption tracker all qualify. A blog,
    PDF scraper, or contractor forum does not. See `citation_is_allowed()`.

    The per-jurisdiction half matters more than it looks: the static list is
    essentially ".gov", and only 84 of the 273 Colorado cities in
    jurisdictions.json are on .gov — the rest are .org, .com and .us. Trusting
    each jurisdiction for its OWN domain, rather than widening the static list
    to whole TLDs, is what keeps contractor blogs out.

The verify pipeline uses these constants directly and mocks
`agents.perplexity.search_json` in tests.
"""
import re
from urllib.parse import urlparse


PROMPT_TEMPLATE = (
    'You are verifying the roofing code for a specific Colorado permitting '
    'authority so a roofing contractor can put accurate code information in '
    'front of a homeowner before signing.\n\n'
    'Jurisdiction: {name}\n'
    'Kind: {kind}\n'
    'County: {county}\n'
    'Known building-department page: {url}\n\n'
    'Return a JSON object with these keys and nothing else:\n'
    '{{\n'
    '  "adopted_code": the code edition governing a RESIDENTIAL RE-ROOF here, '
    'in as few words as possible — "2021 IRC", "2024 IRC", or a named regional '
    'code such as "2023 Pikes Peak Regional Building Code". Name ONE code. Do '
    'not add the commercial IBC, effective dates, transition plans or any '
    'explanation; those belong in "amendments". Use "unknown" only when no '
    'authoritative source states it,\n'
    '  "adopted_code_source_url": one URL string or "unknown",\n'
    '  "amendments": [\n'
    '    {{"topic": "short label", "text": "one sentence, plain English",\n'
    '     "source_url": "URL where this amendment is published"}}\n'
    '  ],\n'
    '  "reroof_permit": {{\n'
    '    "submittal_method": "e.g. Online via Citizen Access Portal" or "unknown",\n'
    '    "portal_url":       "URL of the submittal portal" or "unknown",\n'
    '    "fee_basis":        "e.g. per-square fee, valuation-based, flat" or "unknown"\n'
    '  }},\n'
    '  "issues_permits_for_roofing": true | false,\n'
    '  "delegated_to": "name of the authority that actually issues them, '
    'when the named jurisdiction contracts inspections out" or null\n'
    '}}\n\n'
    'Every URL you cite MUST be a real, current page you would open. Prefer '
    'the jurisdiction\'s own domain, the published municipal code on '
    'library.municode.com / codelibrary.amlegal.com / ecode360.com, or the '
    'ICC code adoption tracker. Do NOT cite blogs, contractor forums, or '
    'search-result pages. If you cannot find the value from an authoritative '
    'source, use "unknown" — do not guess.'
)


ALLOWED_CITATION_HOSTS = (
    '.gov',
    'municode.com',
    'amlegal.com',
    'ecode360.com',
    'sterlingcodifiers.com',
    'iccsafe.org',
    'codes.iccsafe.org',
    # Code publishers Colorado municipalities actually use. colocode.com is
    # Colorado Code Publishing, who codify a large share of the small towns in
    # jurisdictions.json; municipal.codes hosts Aurora among others.
    'municipal.codes',
    'colocode.com',
    'codepublishing.com',
    'generalcode.com',
)


def build_prompt(jurisdiction):
    """Format PROMPT_TEMPLATE against one jurisdictions.json entry."""
    return PROMPT_TEMPLATE.format(
        name   = (jurisdiction.get('name')   or '').strip() or 'unknown',
        kind   = (jurisdiction.get('kind')   or '').strip() or 'unknown',
        county = (jurisdiction.get('county') or '').strip() or 'unknown',
        url    = (jurisdiction.get('url')    or '').strip() or '(none on file)',
    )


def _host_of(url):
    """Lowercase hostname, or '' for anything without a real http(s) host
    (data:, javascript:, empty string). Callers treat '' as fail-closed."""
    if not url:
        return ''
    try:
        return (urlparse(str(url)).hostname or '').lower()
    except (ValueError, TypeError):
        return ''


def _unwrap_archive(url):
    """`https://web.archive.org/web/20200522064708/https://townofx.gov/` →
    `https://townofx.gov/`.

    30 entries in jurisdictions.json list a 2020 Wayback snapshot as the
    town's official site (an artifact of how the file was generated). The
    snapshot host is web.archive.org, which tells us nothing about who the
    town is, so we look through it to the URL that was archived."""
    s = str(url or '').strip()
    if 'web.archive.org' not in s.lower():
        return s
    for marker in ('/http://', '/https://'):
        i = s.find(marker)
        if i >= 0:
            return s[i + 1:]
    return s


# Suffixes that are not anybody's own domain. Guards jurisdiction_hosts()
# against a malformed url turning into a wildcard.
_PUBLIC_SUFFIXES = frozenset({
    'us', 'co.us', 'ci.co.us', 'state.co.us', 'gov', 'org', 'com', 'net',
})


def _name_slug(name):
    """'Douglas County' -> 'douglas', 'City of Fort Collins' -> 'fortcollins'.
    Used only to derive the `<name>.co.us` locality domain."""
    n = str(name or '').strip()
    for prefix in ('City and County of ', 'City of ', 'Town of ', 'County of '):
        if n.startswith(prefix):
            n = n[len(prefix):]
            break
    if n.endswith(' County'):
        n = n[:-len(' County')]
    return re.sub(r'[^a-z0-9]+', '', n.lower())


def jurisdiction_hosts(jurisdiction):
    """The domains this specific jurisdiction publishes on, from its own
    `url`/`code_url` in jurisdictions.json.

    This is the fix for the allowlist's original blind spot: it assumed a
    Colorado municipality lives on `.gov`, but across the 273 cities in the
    file the split is .org 90, .gov 84, .com 74, .us 18. Under a bare `.gov`
    rule the allowlist rejected 69% of cities' OWN official sites — Aurora's
    real building-code page at auroragov.org was thrown out as untrustworthy.

    We do NOT widen the static list to `.org`/`.com` (that would admit any
    contractor blog). We trust one extra domain per jurisdiction: the one
    already recorded for it. Returns bare hostnames; `citation_is_allowed`
    matches them exactly or as a parent of a subdomain."""
    out = []
    if not isinstance(jurisdiction, dict):
        return out
    for key in ('url', 'code_url'):
        host = _host_of(_unwrap_archive(jurisdiction.get(key)))
        if host.startswith('www.'):
            host = host[4:]
        # A bare public suffix (someone stored 'http://co.us/') would allow
        # half the internet. Require at least one label in front of it.
        if host and host not in out and host not in _PUBLIC_SUFFIXES:
            out.append(host)
    # All 64 counties in jurisdictions.json have no `url` at all, so they got
    # no domain of their own and fell back to the static list — Douglas County
    # failed while Perplexity was citing apps.douglas.co.us. `.co.us` is the
    # locality space the state delegates, so `<name>.co.us` for the matching
    # name is that jurisdiction. This is strictly narrower than the `.gov`
    # wildcard the static list already grants every jurisdiction.
    slug = _name_slug(jurisdiction.get('name'))
    if slug:
        guess = slug + '.co.us'
        if guess not in out:
            out.append(guess)
    return out


def citation_is_allowed(url, extra_hosts=()):
    """True if `url` is on ALLOWED_CITATION_HOSTS, or on one of `extra_hosts`
    (the jurisdiction's own domains — see `jurisdiction_hosts`).

    We match on host suffix so `foo.co.gov` counts as `.gov` and
    `library.municode.com` counts as `municode.com`. Anything without a real
    http(s) host (data:, javascript:, empty string) fails closed."""
    host = _host_of(url)
    if not host:
        return False
    for suffix in ALLOWED_CITATION_HOSTS:
        s = suffix.lower()
        if s.startswith('.'):
            if host.endswith(s) or host == s[1:]:
                return True
        else:
            if host == s or host.endswith('.' + s):
                return True
    for extra in (extra_hosts or ()):
        e = str(extra or '').strip().lower()
        # Exact host, or a subdomain of it. Never a bare suffix match, so
        # 'auroragov.org' can never be satisfied by 'notauroragov.org'.
        if e and e not in _PUBLIC_SUFFIXES and (host == e or host.endswith('.' + e)):
            return True
    return False


def filter_allowed_citations(urls, extra_hosts=()):
    """Return the subset of `urls` (list of strings) that pass the allowlist,
    preserving order and dropping dupes."""
    out, seen = [], set()
    for u in (urls or []):
        s = str(u).strip()
        if s in seen or not citation_is_allowed(s, extra_hosts):
            continue
        seen.add(s)
        out.append(s)
    return out
