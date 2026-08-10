"""One-time Google Business Profile OAuth consent — run locally, never on Railway.

    python -m agents.scripts.gbp_oauth

Prints a refresh token to YOUR terminal so you can paste it into Railway as
``GBP_OAUTH_REFRESH_TOKEN``. Deliberately:

  * writes nothing to disk — no token cache, no credentials file, nothing that
    could be committed by accident;
  * takes the client secret via getpass, so it never lands in shell history;
  * listens on 127.0.0.1 only, so the callback never leaves the machine.

Business Profile is the one connection that cannot use a service account —
Google's Business Profile APIs don't support them. See agents/CONNECTIONS.md.
"""
import getpass
import http.server
import secrets
import socket
import sys
import threading
import urllib.parse
import webbrowser

import requests

AUTH_URL  = 'https://accounts.google.com/o/oauth2/v2/auth'
TOKEN_URL = 'https://oauth2.googleapis.com/token'
# The only scope Google publishes for the Business Profile APIs. It is
# write-capable; Nimbus itself only ever issues GETs. See CONNECTIONS.md.
SCOPE = 'https://www.googleapis.com/auth/business.manage'


class _Handler(http.server.BaseHTTPRequestHandler):
    result = {}

    def do_GET(self):                                            # noqa: N802
        q = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
        _Handler.result = {k: v[0] for k, v in q.items()}
        ok = 'code' in _Handler.result
        self.send_response(200)
        self.send_header('Content-Type', 'text/html; charset=utf-8')
        self.end_headers()
        self.wfile.write(
            b'<h2>Done - you can close this tab and return to the terminal.</h2>'
            if ok else
            b'<h2>No authorization code came back. Check the terminal.</h2>')

    def log_message(self, *a):       # silence the default stderr access log
        pass


def _free_port():
    with socket.socket() as s:
        s.bind(('127.0.0.1', 0))
        return s.getsockname()[1]


def main():
    print('Google Business Profile — one-time OAuth consent\n')
    print('From Cloud Console > APIs & Services > Credentials, using an OAuth')
    print('client of type "Desktop app".\n')

    client_id = input('Client ID: ').strip()
    if not client_id:
        print('No client ID. Aborting.')
        return 1
    # getpass so the secret is not echoed and does not reach shell history.
    client_secret = getpass.getpass('Client secret (hidden): ').strip()
    if not client_secret:
        print('No client secret. Aborting.')
        return 1

    port = _free_port()
    redirect_uri = f'http://127.0.0.1:{port}'
    state = secrets.token_urlsafe(16)

    server = http.server.HTTPServer(('127.0.0.1', port), _Handler)
    threading.Thread(target=server.handle_request, daemon=True).start()

    params = {
        'client_id': client_id,
        'redirect_uri': redirect_uri,
        'response_type': 'code',
        'scope': SCOPE,
        'access_type': 'offline',      # required to get a refresh token
        'prompt': 'consent',           # force one even on a repeat authorisation
        'state': state,
    }
    url = f'{AUTH_URL}?{urllib.parse.urlencode(params)}'
    print('\nOpening your browser. Sign in as the account that MANAGES the')
    print('Business Profile — a personal account without access will authorise')
    print('fine and then read nothing.\n')
    print(f'If it does not open: {url}\n')
    webbrowser.open(url)

    server.serve_forever_timeout = None
    threading.Event().wait(1)
    # handle_request already returned once the browser hit the callback; give
    # the user time to actually complete consent.
    print('Waiting for the callback…')
    for _ in range(300):                       # up to ~5 minutes
        if _Handler.result:
            break
        threading.Event().wait(1)

    result = _Handler.result
    if not result:
        print('Timed out waiting for consent.')
        return 1
    if result.get('state') != state:
        print('State mismatch — discarding this response rather than trusting it.')
        return 1
    if 'code' not in result:
        print(f'Authorisation failed: {result.get("error", "no code returned")}')
        return 1

    r = requests.post(TOKEN_URL, timeout=30, data={
        'code': result['code'],
        'client_id': client_id,
        'client_secret': client_secret,
        'redirect_uri': redirect_uri,
        'grant_type': 'authorization_code',
    })
    if not r.ok:
        print(f'Token exchange failed: HTTP {r.status_code}')
        return 1
    refresh = (r.json() or {}).get('refresh_token', '')
    if not refresh:
        print('Google returned no refresh_token. This usually means the account')
        print('has authorised before — revoke at')
        print('https://myaccount.google.com/permissions and run this again.')
        return 1

    print('\n' + '=' * 68)
    print('Set these three in Railway (Variables tab). Nothing was saved here.')
    print('=' * 68)
    print(f'GBP_OAUTH_CLIENT_ID     = {client_id}')
    print(f'GBP_OAUTH_CLIENT_SECRET = (the secret you just typed)')
    print(f'GBP_OAUTH_REFRESH_TOKEN = {refresh}')
    print('=' * 68)
    print('\nThen redeploy and hit Re-check on Nimbus > Connections.')
    print('Close this terminal when you are done — the token above is a')
    print('long-lived credential.')
    return 0


if __name__ == '__main__':
    sys.exit(main())
