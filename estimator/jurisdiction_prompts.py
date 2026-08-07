"""Perplexity prompt template + citation allowlist for jurisdiction code lookup.

Kept in its own file so the exact prompt is easy to audit, iterate on, and
test in isolation. The rules encoded here are what stop model hallucination
from reaching a customer:

  * The schema is fixed. Missing values become the literal string "unknown"
    (matches `agents.perplexity.search_json`'s system-append), never a guess.
  * At least one citation returned by Perplexity must resolve to a host on
    ALLOWED_CITATION_HOSTS. A city building department page (.gov), the
    published municipal code (Municode / American Legal / eCode360 /
    Sterling), or the ICC code adoption tracker all qualify. A blog, PDF
    scraper, or contractor forum does not. See `citation_is_allowed()`.

The verify pipeline uses these constants directly and mocks
`agents.perplexity.search_json` in tests.
"""
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
    '  "adopted_code": "e.g. IRC 2021" or "unknown",\n'
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
)


def build_prompt(jurisdiction):
    """Format PROMPT_TEMPLATE against one jurisdictions.json entry."""
    return PROMPT_TEMPLATE.format(
        name   = (jurisdiction.get('name')   or '').strip() or 'unknown',
        kind   = (jurisdiction.get('kind')   or '').strip() or 'unknown',
        county = (jurisdiction.get('county') or '').strip() or 'unknown',
        url    = (jurisdiction.get('url')    or '').strip() or '(none on file)',
    )


def citation_is_allowed(url):
    """True if `url` is on ALLOWED_CITATION_HOSTS.

    We match on host suffix so `foo.co.gov` counts as `.gov` and
    `library.municode.com` counts as `municode.com`. Anything without a real
    http(s) host (data:, javascript:, empty string) fails closed."""
    if not url:
        return False
    try:
        host = (urlparse(str(url)).hostname or '').lower()
    except (ValueError, TypeError):
        return False
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
    return False


def filter_allowed_citations(urls):
    """Return the subset of `urls` (list of strings) that pass the allowlist,
    preserving order and dropping dupes."""
    out, seen = [], set()
    for u in (urls or []):
        s = str(u).strip()
        if s in seen or not citation_is_allowed(s):
            continue
        seen.add(s)
        out.append(s)
    return out
