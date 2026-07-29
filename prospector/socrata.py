"""Minimal Socrata (SODA 2.1) reader for Colorado's open-data portal.

Both datasets we use are free and need no key. An app token only lifts the
anonymous rate limit — set SOCRATA_APP_TOKEN if a big pull starts getting 429s.
"""
import os
import time

import requests

BASE = 'https://data.colorado.gov/resource'

# Socrata caps an anonymous page at 1000 rows.
PAGE_SIZE = 1000


def quote(value):
    """A SoQL string literal. Doubling the quote is the only escape SoQL has.

    Without this, "Home Owner's Association" — the licence type behind the
    single largest free segment — is a syntax error.
    """
    return "'" + str(value).replace("'", "''") + "'"


def any_of(field, values):
    """A SoQL `field in ('a','b')` clause."""
    return f'{field} in ({", ".join(quote(v) for v in values)})'


def fetch(dataset, where=None, select=None, order=None, limit=None,
          app_token=None, session=None):
    """Yield rows from `dataset`, paginating until exhausted or `limit` is hit.

    `order` is forced to a stable column because Socrata does not guarantee
    paging order otherwise — without it a long pull silently repeats and skips
    rows across pages.
    """
    sess = session or requests.Session()
    token = app_token or os.environ.get('SOCRATA_APP_TOKEN', '')
    headers = {'X-App-Token': token} if token else {}

    sent = 0
    offset = 0
    while True:
        page = PAGE_SIZE if limit is None else min(PAGE_SIZE, limit - sent)
        if page <= 0:
            return
        params = {'$limit': page, '$offset': offset, '$order': order or ':id'}
        if where:
            params['$where'] = where
        if select:
            params['$select'] = select

        rows = _get(sess, f'{BASE}/{dataset}.json', params, headers)
        if not rows:
            return
        for row in rows:
            yield row
        sent += len(rows)
        offset += len(rows)
        if len(rows) < page or (limit is not None and sent >= limit):
            return


def _get(sess, url, params, headers, attempts=4):
    """GET with a backoff, because an unauthenticated pull will meet a 429."""
    for attempt in range(attempts):
        r = sess.get(url, params=params, headers=headers, timeout=60)
        if r.status_code == 429 and attempt < attempts - 1:
            time.sleep(2 ** attempt)
            continue
        r.raise_for_status()
        return r.json()
    return []


def count(dataset, where=None, app_token=None):
    """Row count for a filter — cheap enough to run before a big pull."""
    rows = list(fetch(dataset, where=where, select='count(1) AS n',
                      order='n', limit=1, app_token=app_token))
    return int(rows[0]['n']) if rows else 0
