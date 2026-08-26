"""Read-only status probes for the marketing data connections.

Powers the Nimbus "Marketing Connections" page. Answers one question per
connector — can we read this, right now — and nothing else.

Three hard rules, each with a test behind it:

  * **Read-only.** Every data call in this module is an HTTP GET. The single
    POST is the OAuth token exchange, which mints a credential and touches no
    marketing data. ``test_connections_module_makes_no_write_calls`` greps this
    file and fails on any other write verb.
  * **Secrets never leave the server.** ``status_all()`` returns env var
    *names*, booleans, and non-secret identifiers. No key, token, secret or
    private key is ever placed in a response.
  * **Never raises.** A broken connector is a red row on the dashboard, not a
    500. Every probe is wrapped.

Credentials come from Railway env vars only — nothing is read from a file
committed to the repo, and nothing is written back.
"""
import base64
import json
import os
import time
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import quote

try:
    import requests
except ImportError:                            # pragma: no cover - dev only
    requests = None

# google-auth signs the service-account JWT. Optional import so the portal
# still boots (and this page still renders, showing "not connected") on an
# environment where the dependency hasn't landed yet.
try:
    from google.oauth2 import service_account as _google_sa
    from google.auth.transport.requests import Request as _GoogleRequest
except ImportError:                            # pragma: no cover - optional dep
    _google_sa = None
    _GoogleRequest = None


CONNECTED     = 'connected'
NOT_CONNECTED = 'not_connected'
ERROR         = 'error'
# GA4 and Search Console are owned at the franchise level and cannot be
# granted to us. They are OPTIONAL FUTURE ENHANCEMENTS, not breakage — a red
# "error" row would be a standing lie about something nobody can fix, and a
# dashboard that always shows two failures teaches people to ignore it. If the
# franchise ever grants access, setting the env vars flips these to a normal
# probe with no code change.
OWNER_REQUIRED = 'owner_required'
# Gated by the provider rather than by us. Reddit's Responsible Builder Policy
# requires explicit written approval before any API access, and separately
# requires it for commercial use — which a roofing company's marketing plainly
# is. Distinct from "not configured": no amount of setting env vars fixes it.
APPROVAL_REQUIRED = 'approval_required'
# The source is ours and needs no credential, but it did not answer this time.
# GDELT is a free public endpoint with no key, no account and no quota: it
# rate-limits and times out transiently, and there is nothing on our side to
# configure when it does. Red is reserved for something a person can act on,
# so this is amber and says so. Distinct from CONNECTED, which would overclaim
# — we did not get data — and from ERROR, which would send someone hunting for
# a fault that is not here.
DEGRADED      = 'degraded'
# Config looks complete but nothing has been verified against Google. Only
# ever produced by status_all(probe=False), which the dashboard does not use —
# the page always probes, so this never renders as a status. Claiming
# "connected" on the strength of a set env var would be a lie.
UNCHECKED     = 'unchecked'

_TIMEOUT = 15
_OAUTH_TOKEN_URL = 'https://oauth2.googleapis.com/token'

# Read-only scopes. GA4 and Search Console both publish one; Google Business
# Profile does NOT — see GBP_SCOPE below.
GA4_SCOPE = 'https://www.googleapis.com/auth/analytics.readonly'
GSC_SCOPE = 'https://www.googleapis.com/auth/webmasters.readonly'
# Google publishes no read-only scope for the Business Profile APIs. This one
# scope grants write access too, so read-only for GBP is enforced by OUR code
# (this module only ever issues GETs), not by the scope. Flagged in the UI.
GBP_SCOPE = 'https://www.googleapis.com/auth/business.manage'

# One service account covers GA4 + Search Console, so there is one key to
# rotate. Base64 is preferred: a PEM private key has newlines, and pasting it
# raw into a dashboard env var field is where it usually gets mangled.
SA_ENV_B64 = 'GOOGLE_SERVICE_ACCOUNT_JSON_B64'
SA_ENV_RAW = 'GOOGLE_SERVICE_ACCOUNT_JSON'


# ── The registry ─────────────────────────────────────────────────────────────
# Declarative on purpose: this is the single source of truth for the setup
# checklist, the env var names, and the status page. Adding a connector here
# makes it appear on the dashboard.

CONNECTORS = [
    {
        'key':   'ga4',
        'label': 'Google Analytics 4',
        'auth':  'service_account',
        'secret_env': [SA_ENV_B64],
        'id_env': [
            {'name': 'GA4_PROPERTY_ID', 'example': '123456789',
             'where': 'GA4 → Admin → Property Settings → Property ID (digits only, no "properties/" prefix)'},
        ],
        'scopes': [GA4_SCOPE],
        'scope_is_readonly': True,
        'requires_owner': True,
        'tier': 'optional_future',
        'blocked_reason': 'Owner access required — the GA4 property is owned at '
                          'the franchise level. Only a property Administrator can '
                          'add the service account, and we are not one.',
        'grant': 'Requires a franchise Administrator: Admin → Property Access '
                 'Management → add the service account email as a Viewer.',
        'reads': 'Sessions, traffic sources, landing pages, conversions.',
    },
    {
        'key':   'gsc',
        'label': 'Google Search Console',
        'auth':  'service_account',
        'secret_env': [SA_ENV_B64],
        'id_env': [
            {'name': 'GSC_SITE_URL', 'example': 'sc-domain:projectoneroofingcolorado.com',
             'where': 'Search Console → property picker. A Domain property is '
                      '"sc-domain:projectoneroofingcolorado.com"; a URL-prefix property is '
                      'the full URL with trailing slash.'},
        ],
        'scopes': [GSC_SCOPE],
        'scope_is_readonly': True,
        'requires_owner': True,
        'tier': 'optional_future',
        'blocked_reason': 'Owner access required — the Search Console property is '
                          'owned at the franchise level. Only an Owner can add '
                          'users, and we hold no role on it.',
        'grant': 'Requires a franchise Owner: Settings → Users and permissions → '
                 'add the service account email as a Restricted user. '
                 'Alternative: self-verify a URL-prefix property via a meta tag '
                 'in the CMS, which grants Owner without franchise involvement.',
        'reads': 'Queries, impressions, clicks, average position, indexed pages.',
    },
    {
        'key':   'gbp',
        'label': 'Google Business Profile',
        'auth':  'oauth',
        'secret_env': ['GBP_OAUTH_CLIENT_ID', 'GBP_OAUTH_CLIENT_SECRET',
                       'GBP_OAUTH_REFRESH_TOKEN'],
        'id_env': [
            # Optional, and that is load-bearing: you discover this ID BY
            # connecting, so requiring it would gate the probe on its own
            # output. The probe lists accounts and reports the IDs it finds.
            {'name': 'GBP_ACCOUNT_ID', 'example': '106...', 'optional': True,
             'where': 'Discovered by connecting — the probe below reports it, and '
                      '`python -m agents.scripts.gbp_oauth` prints it. Set it to '
                      'pin reads to one account.'},
            {'name': 'GBP_LOCATION_ID', 'example': '123...', 'optional': True,
             'where': 'Digits after "locations/". Leave unset to read every '
                      'location on the account.'},
        ],
        'scopes': [GBP_SCOPE],
        'scope_is_readonly': False,
        'grant': 'OAuth consent by a Google account that manages the Business '
                 'Profile. Service accounts are NOT supported by these APIs. '
                 'Access also requires Google to approve the Business Profile '
                 'API request form.',
        'reads': 'Locations, reviews, and profile performance (calls, direction '
                 'requests, searches).',
        'caveat': 'Google publishes no read-only scope for Business Profile. '
                  'business.manage can write, so read-only here is enforced by '
                  'Nimbus (GET only), not by Google.',
    },
    {
        'key':   'bing',
        'label': 'Bing Webmaster Tools',
        'auth':  'api_key',
        'secret_env': ['BING_WEBMASTER_API_KEY'],
        'id_env': [
            {'name': 'BING_SITE_URL', 'example': 'https://projectoneroofingcolorado.com',
             'where': 'The site exactly as it appears in Bing Webmaster Tools.'},
        ],
        'scopes': [],
        'scope_is_readonly': True,
        'grant': 'bing.com/webmasters → add the site → verify with a meta tag '
                 'through the CMS (verification is independent of Google, so '
                 'this needs nobody outside the Colorado team) → Settings → '
                 'API access → API Key.',
        'reads': 'Real search queries, impressions, clicks and average position. '
                 'The only MEASURED search data available to us.',
        'caveat': 'Bing is a minority of search. Its figures are real and are '
                  'always labelled Bing — never presented as "search" or as a '
                  'stand-in for Google.',
    },
    {
        'key':   'reddit',
        'label': 'Reddit (customer language)',
        'auth':  'api_key',
        'secret_env': ['REDDIT_CLIENT_ID', 'REDDIT_CLIENT_SECRET'],
        'id_env': [
            {'name': 'REDDIT_USER_AGENT', 'optional': True,
             'example': 'python:project-one-nimbus:v1.0 (contact ...)',
             'where': 'Optional. Reddit throttles generic user-agents; a default '
                      'identifying one is used if unset.'},
        ],
        'scopes': [],
        'scope_is_readonly': True,
        'requires_approval': True,
        'tier': 'blocked_external',
        'blocked_reason': "Approval required — Reddit's Responsible Builder "
                          'Policy (checked 2026-08-11) requires explicit approval '
                          'before ANY API access, and separate written approval '
                          'for commercial use. Marketing for a roofing company is '
                          'commercial. The old self-serve script-app flow at '
                          '/prefs/apps now bounces you to the Devvit developer '
                          'platform, which builds apps that run ON Reddit — not '
                          'this. Setting the env vars will not help.',
        'grant': 'Would need written commercial approval from Reddit '
                 '(support.reddithelp.com → contact). Not realistically '
                 'obtainable for a single contractor. Reading those subreddits '
                 'in a browser by hand is unaffected and entirely fine — it is '
                 'automated API access that is gated.',
        'reads': 'Would read: top weekly posts in Colorado and home-improvement '
                 'subreddits, filtered to roofing keywords. Built and tested; '
                 'switches on if approval is ever granted.',
        'caveat': 'Code is complete and covered by tests. It stays in place so '
                  'that if approval ever arrives it is two env vars away — but '
                  'nobody should treat it as merely unconfigured.',
    },
    {
        'key':   'gdelt',
        'label': 'GDELT (Colorado news + storm events)',
        'auth':  'none',
        'secret_env': [],
        'id_env': [],
        'scopes': [],
        'scope_is_readonly': True,
        'grant': 'None. Free public endpoint, no key and no account.',
        'reads': 'Colorado news matching roofing, hail and storm keywords. The '
                 'only source that lets copy cite a real, dated weather event '
                 'instead of staying silent about one.',
    },
    {
        'key':   'website',
        'label': 'Website / CMS',
        'auth':  'none',
        'secret_env': [],
        'id_env': [
            {'name': 'MARKETING_SITE_URL', 'example': 'https://projectoneroofingcolorado.com',
             'optional': True,
             'where': 'Defaults to the website in agents/marketing_profile.json.'},
        ],
        'scopes': [],
        'scope_is_readonly': True,
        'grant': 'None. v1 reads the public sitemap only — no CMS credential, '
                 'no login, nothing to grant.',
        'reads': 'Public sitemap.xml: which pages exist and when they changed.',
    },
]

# Declared but not yet specified. Shown on the page as an open item rather than
# quietly omitted, so it doesn't get forgotten — but not invented into a fake
# connector either.
UNDEFINED_CONNECTORS = [
    {
        'key':   'co_marketing_data',
        'label': 'Colorado marketing data',
        'auth':  'unknown',
        'needs': 'Where this lives has not been specified — a spreadsheet, an '
                 'ad account, a BI export, or something else. Name the system '
                 'and this becomes a real connector.',
    },
]


# ── Credentials (server-side only, never returned) ───────────────────────────

def _service_account_info():
    """Parse the service account JSON out of env. Returns None when unset.

    Raises ValueError on malformed input so the connector reports a precise
    error instead of a bare "not connected".
    """
    raw = os.environ.get(SA_ENV_B64, '').strip()
    if raw:
        try:
            decoded = base64.b64decode(raw, validate=True).decode('utf-8')
        except Exception as e:                                   # noqa: BLE001
            raise ValueError(f'{SA_ENV_B64} is not valid base64: {e}') from e
    else:
        decoded = os.environ.get(SA_ENV_RAW, '').strip()
    if not decoded:
        return None
    try:
        info = json.loads(decoded)
    except ValueError as e:
        raise ValueError(f'service account JSON does not parse: {e}') from e
    if not isinstance(info, dict) or not info.get('client_email'):
        raise ValueError('service account JSON has no client_email — is this '
                         'an OAuth client file rather than a service account key?')
    return info


def service_account_email():
    """The SA's email — a non-secret identifier, and the one you must grant.

    Safe to display: it is an address you paste into Google's own sharing
    dialogs. The private_key alongside it never leaves this process.
    """
    try:
        info = _service_account_info()
    except ValueError:
        return ''
    return (info or {}).get('client_email', '')


_sa_token_cache = {}      # scope tuple -> (token, expires_at_epoch)


def _sa_access_token(scopes):
    """Mint (and briefly cache) a service-account access token."""
    if _google_sa is None:
        raise RuntimeError(
            'the google-auth package is not installed — add google-auth to '
            'requirements.txt and redeploy')
    key = tuple(sorted(scopes))
    hit = _sa_token_cache.get(key)
    if hit and hit[1] > time.time() + 60:
        return hit[0]
    info = _service_account_info()
    if not info:
        raise RuntimeError(f'{SA_ENV_B64} is not set')
    creds = _google_sa.Credentials.from_service_account_info(info, scopes=list(scopes))
    creds.refresh(_GoogleRequest())
    expiry = creds.expiry.timestamp() if creds.expiry else time.time() + 3000
    _sa_token_cache[key] = (creds.token, expiry)
    return creds.token


def _oauth_access_token():
    """Exchange the stored refresh token for an access token.

    The one POST in this module. It hits Google's token endpoint to mint a
    credential — it reads and writes no marketing data. The refresh token comes
    from a one-time consent run outside this app (see agents/CONNECTIONS.md)
    and is never logged or returned.
    """
    if requests is None:
        raise RuntimeError('the `requests` package is not installed')
    client_id     = os.environ.get('GBP_OAUTH_CLIENT_ID', '').strip()
    client_secret = os.environ.get('GBP_OAUTH_CLIENT_SECRET', '').strip()
    refresh_token = os.environ.get('GBP_OAUTH_REFRESH_TOKEN', '').strip()
    missing = [n for n, v in (('GBP_OAUTH_CLIENT_ID', client_id),
                              ('GBP_OAUTH_CLIENT_SECRET', client_secret),
                              ('GBP_OAUTH_REFRESH_TOKEN', refresh_token)) if not v]
    if missing:
        raise RuntimeError(f'not set: {", ".join(missing)}')
    r = requests.post(_OAUTH_TOKEN_URL, timeout=_TIMEOUT, data={
        'client_id': client_id, 'client_secret': client_secret,
        'refresh_token': refresh_token, 'grant_type': 'refresh_token'})
    if not r.ok:
        # Google echoes the client_id in some error bodies; report the code and
        # the short reason only, never the raw body.
        reason = ''
        try:
            reason = (r.json() or {}).get('error', '')
        except ValueError:
            pass
        raise RuntimeError(f'token exchange failed (HTTP {r.status_code}'
                           + (f': {reason}' if reason else '') + ')')
    token = (r.json() or {}).get('access_token', '')
    if not token:
        raise RuntimeError('token endpoint returned no access_token')
    return token


def _get(url, token=None, params=None):
    """The only data-fetching call in this module. GET, always."""
    if requests is None:
        raise RuntimeError('the `requests` package is not installed')
    headers = {'Authorization': f'Bearer {token}'} if token else {}
    return requests.get(url, headers=headers, params=params or {}, timeout=_TIMEOUT)


def _http_error(r):
    """Turn a failed response into a short operator-readable reason.

    Deliberately does not include the response body: Google error payloads can
    echo request parameters back, and this string is rendered in a browser.
    """
    if r.status_code in (401, 403):
        return (f'HTTP {r.status_code} — authenticated but not authorised. '
                f'Check the account has been granted access to this property.')
    if r.status_code == 404:
        return f'HTTP 404 — not found. Check the ID is exactly right.'
    if r.status_code == 429:
        return 'HTTP 429 — rate limited by Google. Try again shortly.'
    return f'HTTP {r.status_code}'


# ── Probes: one read-only GET each ───────────────────────────────────────────

def _probe_ga4():
    prop = os.environ.get('GA4_PROPERTY_ID', '').strip().replace('properties/', '')
    if not prop:
        return NOT_CONNECTED, 'GA4_PROPERTY_ID is not set'
    if not _service_account_info():
        return NOT_CONNECTED, f'{SA_ENV_B64} is not set'
    token = _sa_access_token([GA4_SCOPE])
    r = _get(f'https://analyticsdata.googleapis.com/v1beta/properties/{quote(prop, safe="")}/metadata',
             token=token)
    if not r.ok:
        return ERROR, _http_error(r)
    n = len((r.json() or {}).get('metrics') or [])
    return CONNECTED, f'property {prop} readable — {n} metrics available'


def _probe_gsc():
    site = os.environ.get('GSC_SITE_URL', '').strip()
    if not site:
        return NOT_CONNECTED, 'GSC_SITE_URL is not set'
    if not _service_account_info():
        return NOT_CONNECTED, f'{SA_ENV_B64} is not set'
    token = _sa_access_token([GSC_SCOPE])
    r = _get(f'https://www.googleapis.com/webmasters/v3/sites/{quote(site, safe="")}',
             token=token)
    if not r.ok:
        return ERROR, _http_error(r)
    level = (r.json() or {}).get('permissionLevel', 'unknown')
    return CONNECTED, f'{site} readable — permission: {level}'


def _probe_gbp():
    token = _oauth_access_token()          # raises RuntimeError when unset
    r = _get('https://mybusinessaccountmanagement.googleapis.com/v1/accounts',
             token=token)
    if not r.ok:
        if r.status_code == 403:
            return ERROR, ('HTTP 403 — the Business Profile API is enabled but '
                           'this project may not have approved API access yet.')
        return ERROR, _http_error(r)
    accounts = (r.json() or {}).get('accounts') or []
    if not accounts:
        return ERROR, 'authenticated, but this Google account manages no Business Profile'
    # Report the IDs rather than making someone go find them — this is where
    # GBP_ACCOUNT_ID comes from. Non-secret identifiers, admin-only page.
    ids = ', '.join(a.get('name', '').split('/')[-1] for a in accounts[:5])
    return CONNECTED, f'{len(accounts)} account(s) readable — GBP_ACCOUNT_ID: {ids}'


def _probe_website():
    from . import config
    base = os.environ.get('MARKETING_SITE_URL', '').strip()
    if not base:
        try:
            base = config.load_marketing_profile()['company']['website']
        except Exception:                                        # noqa: BLE001
            return NOT_CONNECTED, 'MARKETING_SITE_URL is not set'
    r = _get(base.rstrip('/') + '/sitemap.xml')
    if not r.ok:
        return ERROR, f'{_http_error(r)} fetching sitemap.xml'
    body = r.text or ''
    if '<' not in body or 'sitemap' not in body.lower():
        return ERROR, 'sitemap.xml did not return XML'
    return CONNECTED, f'sitemap readable — {body.count("<loc>")} URLs listed'


def _probe_reddit():
    from .content.sources import reddit
    if not reddit.available():
        return NOT_CONNECTED, 'REDDIT_CLIENT_ID / REDDIT_CLIENT_SECRET are not set'
    # One cheap subreddit read proves the token exchange and the read scope.
    result = reddit.pull(subs=['Roofing'], limit=5)
    if not result.get('posts') and result.get('note', '').lower().startswith(
            ('reddit rejected', 'reddit token', 'reddit auth')):
        return ERROR, result['note']
    return CONNECTED, result.get('note', 'readable')


def _probe_gdelt():
    from .content.sources import news_gdelt
    result = news_gdelt.pull(days=3, limit=5)
    note = result.get('note', '')
    if result.get('articles') or 'no Colorado matches' in note:
        return CONNECTED, note
    # Rate-limited or unreachable: both are the free endpoint having a moment,
    # both cost a run one input rather than failing it, and neither is
    # fixable from here. `pull` never raises, so a timeout arrives as a note.
    if 'rate-limited' in note or 'unreachable' in note:
        return DEGRADED, note
    return ERROR, note or 'no response'


def _probe_bing():
    from .seo import bing
    result = bing.summary()
    if not result.get('available'):
        note = result.get('note', '')
        if 'not set' in note:
            return NOT_CONNECTED, note
        return ERROR, note
    return CONNECTED, result.get('note', 'readable')


_PROBES = {
    'bing':    _probe_bing,
    'ga4':     _probe_ga4,
    'gsc':     _probe_gsc,
    'gbp':     _probe_gbp,
    'reddit':  _probe_reddit,
    'gdelt':   _probe_gdelt,
    'website': _probe_website,
}


# ── Public surface ───────────────────────────────────────────────────────────

def _describe(conn):
    """The non-secret half of a connector row: names, never values."""
    secret_env = [{'name': n, 'set': bool(os.environ.get(n, '').strip())}
                  for n in conn['secret_env']]
    id_env = [{'name': i['name'],
               'set': bool(os.environ.get(i['name'], '').strip()),
               'value': os.environ.get(i['name'], '').strip(),
               'example': i.get('example', ''),
               'optional': bool(i.get('optional')),
               'where': i.get('where', '')}
              for i in conn['id_env']]
    return {
        'key':   conn['key'],
        'label': conn['label'],
        'auth':  conn['auth'],
        'secret_env': secret_env,          # names + set/unset, no values
        'id_env':     id_env,              # non-secret IDs; safe to echo
        'scopes': conn['scopes'],
        'scope_is_readonly': conn['scope_is_readonly'],
        'grant':  conn['grant'],
        'reads':  conn.get('reads', ''),
        'caveat': conn.get('caveat', ''),
        'tier':   conn.get('tier', 'active'),
        'requires_owner': bool(conn.get('requires_owner')),
        'requires_approval': bool(conn.get('requires_approval')),
        'blocked_reason': conn.get('blocked_reason', ''),
    }


def _run_probe(conn):
    row = _describe(conn)
    missing = [s['name'] for s in row['secret_env'] if not s['set']]
    required_ids = [i['name'] for i in row['id_env']
                    if not i['set'] and not i['optional']]
    if missing or required_ids:
        # A connector gated by someone else is not misconfigured — nobody here
        # can configure it. Report the real reason rather than a list of env
        # vars that would not help.
        if row['requires_owner']:
            row['status'] = OWNER_REQUIRED
            row['detail'] = row['blocked_reason']
            return row
        if row['requires_approval']:
            row['status'] = APPROVAL_REQUIRED
            row['detail'] = row['blocked_reason']
            return row
        row['status'] = NOT_CONNECTED
        row['detail'] = 'not set: ' + ', '.join(missing + required_ids)
        return row
    try:
        status, detail = _PROBES[conn['key']]()
    except RuntimeError as e:
        status, detail = NOT_CONNECTED, str(e)
    except ValueError as e:
        status, detail = ERROR, str(e)
    except Exception as e:                                       # noqa: BLE001
        # Never let a probe take the page down. Report the exception type and
        # message, not a traceback — this renders in a browser.
        status, detail = ERROR, f'{type(e).__name__}: {e}'
    row['status'] = status
    row['detail'] = detail
    return row


def status_all(probe=True):
    """Every connector's current state. Never raises. Never returns a secret.

    ``probe=False`` skips the network entirely and reports configuration only.
    A fully-configured connector comes back ``UNCHECKED``, not ``CONNECTED`` —
    a set env var is not evidence that Google will accept it.
    """
    rows = []
    if probe:
        # Probes are independent network GETs; run them together so the page
        # isn't gated on the slowest one.
        with ThreadPoolExecutor(max_workers=len(CONNECTORS)) as pool:
            rows = list(pool.map(_run_probe, CONNECTORS))
    else:
        for conn in CONNECTORS:
            row = _describe(conn)
            missing = [s['name'] for s in row['secret_env'] if not s['set']]
            missing += [i['name'] for i in row['id_env']
                        if not i['set'] and not i['optional']]
            if missing and row['requires_owner']:
                row['status'], row['detail'] = OWNER_REQUIRED, row['blocked_reason']
            elif missing and row['requires_approval']:
                row['status'], row['detail'] = APPROVAL_REQUIRED, row['blocked_reason']
            elif missing:
                row['status'] = NOT_CONNECTED
                row['detail'] = 'not set: ' + ', '.join(missing)
            else:
                row['status'], row['detail'] = UNCHECKED, 'configured — not verified'
            rows.append(row)

    for u in UNDEFINED_CONNECTORS:
        rows.append({**u, 'secret_env': [], 'id_env': [], 'scopes': [],
                     'scope_is_readonly': True, 'grant': '', 'reads': '',
                     'caveat': '', 'status': NOT_CONNECTED, 'detail': u['needs']})
    return rows


def summary():
    """Page-header facts that cost nothing to compute.

    Deliberately carries no connected/error counts: those depend on a probe,
    and the page derives them from the rows it actually rendered rather than
    from a second, differently-sourced number that could disagree.
    """
    return {
        'total': len(CONNECTORS) + len(UNDEFINED_CONNECTORS),
        'service_account_email': service_account_email(),
        'google_auth_installed': _google_sa is not None,
        'read_only': True,
    }
