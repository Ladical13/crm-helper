"""Nominatim geocoding with an on-disk cache.

Nominatim's usage policy is 1 request/second per app and requires a real
User-Agent identifying the software. We honor both.

Cache is aggressive — an address's lat/lng basically never changes, so a hit
is safe until someone renames a road. TTL is effectively forever unless a
caller passes ``force_refresh=True``.
"""
import hashlib
import time

try:
    import requests
except ImportError:                            # pragma: no cover - dev only
    requests = None

from . import config

_UA = 'P1Nimbus/1.0 (luke@projectoneroofing.com)'
_last_call = [0.0]                             # module-level rate-limiter


def _hash(address):
    return hashlib.sha256((address or '').strip().lower().encode('utf-8')).hexdigest()


def _rate_limit():
    """Nominatim's public tier is 1 req/sec. Sleep the delta if we're too fast."""
    elapsed = time.time() - _last_call[0]
    if elapsed < 1.05:
        time.sleep(1.05 - elapsed)
    _last_call[0] = time.time()


def geocode(address, force_refresh=False):
    """Return ``{lat, lng, city, state, zip, cached}`` or None.

    None means the geocoder didn't find the address. Callers should treat
    that as "no storm join possible" rather than an error.
    """
    address = (address or '').strip()
    if not address:
        return None
    key = _hash(address)

    if not force_refresh:
        with config.get_cache_db() as db:
            row = db.execute(
                'SELECT lat, lng, city, state, zip FROM geocode_cache '
                'WHERE address_hash = ?', (key,)).fetchone()
        if row and row['lat'] is not None:
            return {
                'lat': row['lat'], 'lng': row['lng'],
                'city': row['city'], 'state': row['state'], 'zip': row['zip'],
                'cached': True,
            }
        if row:
            return None   # cached miss

    if requests is None:
        return None
    _rate_limit()
    try:
        r = requests.get('https://nominatim.openstreetmap.org/search',
                         params={'q': address, 'format': 'json', 'limit': 1,
                                 'countrycodes': 'us', 'addressdetails': 1},
                         headers={'User-Agent': _UA}, timeout=15)
    except requests.exceptions.RequestException:
        return None
    if not r.ok:
        return None
    hits = r.json() or []
    if not hits:
        # Cache the miss so we don't hammer Nominatim for an address it can't
        # resolve.
        with config.get_cache_db() as db:
            db.execute(
                'INSERT OR REPLACE INTO geocode_cache '
                '(address_hash, address, lat, lng, created_at) VALUES (?, ?, NULL, NULL, ?)',
                (key, address, config.now_iso()))
            db.commit()
        return None

    hit = hits[0]
    addr = hit.get('address') or {}
    out = {
        'lat':  float(hit['lat']),
        'lng':  float(hit['lon']),
        'city': (addr.get('city') or addr.get('town') or addr.get('village') or ''),
        'state': addr.get('state', '')[:2].upper() if addr.get('state') else '',
        'zip':  addr.get('postcode', '') or '',
        'cached': False,
    }
    with config.get_cache_db() as db:
        db.execute(
            'INSERT OR REPLACE INTO geocode_cache '
            '(address_hash, address, lat, lng, city, state, zip, created_at) '
            'VALUES (?, ?, ?, ?, ?, ?, ?, ?)',
            (key, address, out['lat'], out['lng'], out['city'], out['state'],
             out['zip'], config.now_iso()))
        db.commit()
    return out
