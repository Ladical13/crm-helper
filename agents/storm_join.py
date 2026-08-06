"""Join a lead's address to the canvasser's NOAA hail cache.

The canvasser already ships the whole NOAA SPC hail pipeline
(``canvasser/app.py:354-523``) — the daily CSV fetch, the SQLite cache, the
address-lookup endpoint. Nimbus doesn't duplicate any of it. This module just
calls the canvasser's ``_fetch_hail_days()`` helper directly (in-process; the
canvasser module is loaded by ``portal/wsgi.py``) and computes distance itself
so we don't need an HTTP round-trip.

Returns a short human-readable summary that ends up in ``leads.recent_storm``
and gets rendered as ``{storm_hook}`` in the outreach draft.
"""
from datetime import datetime, timedelta
import math
import sys

# Canvasser is loaded by portal/wsgi.py under this module name.
_CANVASSER_MODULE_NAME = 'p1_canvass_app'


def _canvasser():
    """Return the imported canvasser module, or None if it's not loaded.

    Nimbus works even when running the CLI outside the portal; the storm join
    just becomes a no-op in that case and callers see an empty summary.
    """
    return sys.modules.get(_CANVASSER_MODULE_NAME)


def _haversine_miles(lat1, lng1, lat2, lng2):
    r = 3958.7613
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lng2 - lng1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return r * 2 * math.asin(math.sqrt(a))


def hail_summary(lat, lng, days=730, radius_miles=1.5):
    """Text summary of hail reports near a point in the last ``days``.

    Empty string when no reports (or when the canvasser isn't loaded); reps
    read it as "no recent activity — lead by something else." The distance
    cap defaults to 1.5 miles because a lead's address rarely benefits from
    a hit five miles away, even if it's in the same city.
    """
    canv = _canvasser()
    if canv is None or not hasattr(canv, '_fetch_hail_days'):
        return ''

    # Match canvasser's seasonal + last-45-days scan window so this stays
    # cheap; the cache holds most days already.
    now = datetime.utcnow()
    date_strs = []
    for i in range(1, min(int(days), 1825) + 1):
        d = now - timedelta(days=i)
        if d.month in (3, 4, 5, 6, 7, 8, 9, 10) or i <= 45:
            date_strs.append(d.strftime('%Y%m%d'))
    try:
        results = canv._fetch_hail_days(date_strs)
    except Exception:                           # pragma: no cover - network fail
        return ''

    hits = []
    for ds, feats in (results or {}).items():
        for f in feats:
            coords = ((f or {}).get('geometry') or {}).get('coordinates') or []
            if len(coords) < 2:
                continue
            flat, flon = coords[1], coords[0]
            try:
                dist = _haversine_miles(lat, lng, flat, flon)
            except (TypeError, ValueError):
                continue
            if dist <= radius_miles:
                props = f.get('properties') or {}
                # Size comes as inches * 100 from NOAA (e.g. "200" = 2.00in).
                # canvasser stores it verbatim; render for humans.
                raw_size = props.get('size') or 0
                try:
                    inches = float(raw_size) / 100 if float(raw_size) > 10 else float(raw_size)
                except (TypeError, ValueError):
                    inches = 0.0
                hits.append({
                    'date':   f'{ds[:4]}-{ds[4:6]}-{ds[6:]}',
                    'inches': inches,
                    'dist':   dist,
                })
    if not hits:
        return ''

    hits.sort(key=lambda h: h['inches'], reverse=True)
    biggest = hits[0]
    return (f'{len(hits)} hail event{"s" if len(hits) != 1 else ""} within '
            f'{radius_miles:g} mi in the last {days} days. '
            f'Largest: {biggest["inches"]:.2f}in on {biggest["date"]}.')
