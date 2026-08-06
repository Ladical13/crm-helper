"""Push a rows batch into salescrm through its public importer.

Two call shapes:

  * ``import_via_test_client(...)`` — for in-process use from the Nimbus
    blueprint. Uses Werkzeug's test client on the composed WSGI application
    so all salescrm invariants run: source_ref/phone/email/license dedup,
    live suppression re-check, DNC, batch tracking, cadence auto-materialize,
    ``system`` activity log entry. Zero HTTP hop; zero salescrm bypass.

  * ``import_via_http(...)`` — for the CLI. Same shape as the existing
    prospector's ``push`` module: sign in with a manager username +
    ``PORTAL_PASSWORD``, POST to ``/crm/api/prospects/import``.

Both go through the SAME endpoint. If you find yourself writing an ``INSERT
INTO leads`` in this file, stop — the point is that we never do that.
"""
import json
import os

try:
    import requests
except ImportError:                            # pragma: no cover - dev only
    requests = None


CHUNK = 500


def _chunks(rows, n):
    for i in range(0, len(rows), n):
        yield rows[i:i + n]


def _payload(rows, lead_type, source, rep, batch, dry_run, service='roofing'):
    """Build the JSON body salescrm's importer accepts."""
    return {
        'rows':      rows,
        'lead_type': lead_type,
        'source':    source or 'nimbus',
        'service':   service,
        # rep '' means the endpoint falls back to the current session (which
        # is the admin running Nimbus). A named rep pins every row in the
        # batch to them — that's how per-rep runs stay in the right queue.
        'assign':    rep or '',
        'batch':     batch or '',
        'dry_run':   bool(dry_run),
    }


def _merge_counts(a, b):
    for k in ('inserted', 'duplicate', 'suppressed', 'invalid'):
        a[k] = a.get(k, 0) + int(b.get(k, 0) or 0)


def import_via_test_client(client, rows, *, lead_type, source, rep,
                           batch='', dry_run=False, service='roofing'):
    """Post rows through a Werkzeug ``Client`` (already signed in as admin).

    ``client`` is a ``werkzeug.test.Client`` (or ``flask.testing.FlaskClient``)
    bound to the composed WSGI application. The Nimbus blueprint builds one
    from ``portal.wsgi.application`` and forwards the current admin session,
    so the salescrm importer sees a manager-authenticated request.
    """
    totals  = {'inserted': 0, 'duplicate': 0, 'suppressed': 0, 'invalid': 0}
    batches = []
    details = []
    for chunk in _chunks(list(rows), CHUNK):
        r = client.post('/crm/api/prospects/import',
                        json=_payload(chunk, lead_type, source, rep, batch, dry_run, service))
        if r.status_code not in (200, 201):
            body = r.get_data(as_text=True)[:400]
            raise RuntimeError(f'Import rejected (HTTP {r.status_code}): {body}')
        body = r.get_json() or {}
        _merge_counts(totals, body.get('counts') or {})
        if body.get('batch'):
            batches.append(body['batch'])
        for d in (body.get('details') or []):
            if d.get('status') != 'inserted':
                details.append(d)
    return {'counts': totals, 'batches': sorted(set(batches)),
            'not_inserted': details[:200]}


def import_via_http(rows, *, base_url, username, password=None,
                    lead_type, source, rep, batch='', dry_run=False,
                    service='roofing', on_chunk=None):
    """CLI path: sign in over HTTP then post. Same endpoint as above."""
    if requests is None:
        raise RuntimeError('The `requests` package is not installed.')
    password = password or os.environ.get('PORTAL_PASSWORD')
    if not password:
        raise RuntimeError(
            'PORTAL_PASSWORD not set — cannot sign in to push rows via the API.')
    base = base_url.rstrip('/')
    sess = requests.Session()
    r = sess.post(f'{base}/login',
                  data={'username': username, 'password': password},
                  allow_redirects=False, timeout=30)
    if r.status_code not in (301, 302, 303):
        raise RuntimeError(f'Login failed for {username} (HTTP {r.status_code})')

    totals  = {'inserted': 0, 'duplicate': 0, 'suppressed': 0, 'invalid': 0}
    batches = []
    for chunk in _chunks(list(rows), CHUNK):
        r = sess.post(f'{base}/crm/api/prospects/import',
                      json=_payload(chunk, lead_type, source, rep, batch, dry_run, service),
                      timeout=180)
        if r.status_code not in (200, 201):
            raise RuntimeError(f'Import rejected (HTTP {r.status_code}): {r.text[:400]}')
        body = r.json()
        _merge_counts(totals, body.get('counts') or {})
        if body.get('batch'):
            batches.append(body['batch'])
        if on_chunk:
            on_chunk(len(chunk), body.get('counts') or {})
    return {'counts': totals, 'batches': sorted(set(batches))}


# ── Post-import enrichment write-back ────────────────────────────────────────
# The salescrm importer doesn't know about Nimbus's research columns — they
# were added additively, and staying out of the endpoint's signature means the
# suppression / dedup path never needs to reason about them.
#
# So we go straight to salescrm.db for the *research* update only. This is not
# a lead insert, it's a targeted UPDATE of columns Nimbus itself added on
# rows Nimbus itself just imported. Zero risk of breaking cadence or dedup.

def annotate_leads(salescrm_module, updates):
    """Write research_notes / research_citations / recent_storm / enriched_at.

    ``updates`` is ``[{'lead_id': ..., 'research_notes': ...,
    'research_citations': [...], 'recent_storm': ...}, ...]``.

    Callers should have just imported these leads themselves. Missing IDs are
    silently skipped — a lead that dedup'd against an existing row still gets
    the fresh research attached to *that* row.
    """
    if not updates:
        return 0
    now = salescrm_module._now() if hasattr(salescrm_module, '_now') else config_now()
    n = 0
    with salescrm_module.get_db() as db:
        for u in updates:
            lid = (u.get('lead_id') or '').strip()
            if not lid:
                continue
            citations = u.get('research_citations') or []
            if not isinstance(citations, str):
                citations = json.dumps(citations)
            db.execute(
                'UPDATE leads SET research_notes = ?, research_citations = ?, '
                'recent_storm = ?, enriched_at = ?, updated_at = ? WHERE id = ?',
                (u.get('research_notes') or '', citations,
                 u.get('recent_storm') or '', now, now, lid))
            n += db.total_changes
        db.commit()
    return n


def config_now():
    from . import config
    return config.now_iso()
