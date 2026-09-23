"""Cached, cross-worker rate-limited interactive Nominatim requests."""
import json
import time
from portal import users


def request(http, operation, params, user_agent):
    key = operation + ':' + json.dumps(params, sort_keys=True)
    with users.get_db() as db:
        db.execute('CREATE TABLE IF NOT EXISTS interactive_geo_cache (key TEXT PRIMARY KEY, body TEXT, saved REAL)')
        db.execute('CREATE TABLE IF NOT EXISTS interactive_geo_slot (id INTEGER PRIMARY KEY, next_at REAL)')
        db.commit()
        db.execute('BEGIN IMMEDIATE')
        now = time.time()
        hit = db.execute('SELECT body FROM interactive_geo_cache WHERE key=? AND saved>?',
                         (key, now - 90 * 86400)).fetchone()
        if hit:
            return json.loads(hit['body'])
        slot = db.execute('SELECT next_at FROM interactive_geo_slot WHERE id=1').fetchone()
        # Refuse a burst rather than queuing unbounded web requests.
        if slot and slot['next_at'] > now:
            raise RuntimeError('Address lookup is busy. Please try again in a moment.')
        db.execute('INSERT OR REPLACE INTO interactive_geo_slot VALUES (1,?)', (now + 1.1,))
    response = http.get('https://nominatim.openstreetmap.org/' + operation,
                        params=params, headers={'User-Agent': user_agent}, timeout=10)
    response.raise_for_status()
    body = response.json()
    with users.get_db() as db:
        db.execute('INSERT OR REPLACE INTO interactive_geo_cache VALUES (?,?,?)',
                   (key, json.dumps(body), time.time()))
        db.execute('DELETE FROM interactive_geo_cache WHERE saved<?', (time.time() - 90 * 86400,))
    return body
