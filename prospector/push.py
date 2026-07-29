"""Send a sourced batch to the CRM importer.

Authenticates against the portal exactly as a browser does — POST the login
form, keep the shared session cookie — then posts to /crm/api/prospects/import.
The importer is manager-only, idempotent and suppression-aware, so this stays a
dumb pipe: no dedupe, no filtering, no cleverness on this side.

The password is read from the PORTAL_PASSWORD environment variable or prompted
for. It is never accepted as a command-line argument, where it would land in
shell history and process listings.
"""
import getpass
import json
import os

import requests

# The importer caps a request at 5000 rows; stay well under so a partial
# failure costs one small chunk rather than the whole pull.
CHUNK = 1000


class PushError(RuntimeError):
    pass


def sign_in(base_url, username, password=None):
    """Return a logged-in session, or raise PushError."""
    base = base_url.rstrip('/')
    password = password or os.environ.get('PORTAL_PASSWORD') or getpass.getpass(
        f'Password for {username}: ')
    sess = requests.Session()
    r = sess.post(f'{base}/login',
                  data={'username': username, 'password': password},
                  allow_redirects=False, timeout=30)
    # The portal redirects on success and re-renders the form on failure.
    if r.status_code not in (301, 302, 303):
        raise PushError(f'Login failed for {username} (HTTP {r.status_code})')
    return sess


def push(rows, base_url, session, lead_type, source, assign='', batch='',
         dry_run=False, service='roofing', on_chunk=None):
    """POST rows in chunks. Returns the merged counts across every chunk."""
    base = base_url.rstrip('/')
    url = f'{base}/crm/api/prospects/import'
    totals = {'inserted': 0, 'duplicate': 0, 'suppressed': 0, 'invalid': 0}
    batches, details = [], []

    for start in range(0, len(rows), CHUNK):
        chunk = rows[start:start + CHUNK]
        payload = {'rows': chunk, 'lead_type': lead_type, 'source': source,
                   'service': service, 'assign': assign, 'dry_run': dry_run}
        if batch:
            payload['batch'] = batch
        r = session.post(url, json=payload, timeout=180)
        if r.status_code not in (200, 201):
            raise PushError(f'Import rejected (HTTP {r.status_code}): {r.text[:400]}')
        body = r.json()
        for k in totals:
            totals[k] += body['counts'][k]
        batches.append(body['batch'])
        details.extend(d for d in body['details'] if d['status'] != 'inserted')
        if on_chunk:
            on_chunk(start + len(chunk), len(rows), body['counts'])

    return {'counts': totals, 'batches': sorted(set(batches)),
            'not_inserted': details[:200]}


def load(path):
    """Read a pull file written by `python -m prospector pull`."""
    with open(path, encoding='utf-8') as f:
        doc = json.load(f)
    if isinstance(doc, list):          # a bare row list still works
        return {'rows': doc, 'lead_type': '', 'source': os.path.basename(path)}
    return doc
