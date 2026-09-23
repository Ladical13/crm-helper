"""Backfill coordinates for every address the company owns.

    python -m portal.geocode_backfill              # dry run: what would happen
    python -m portal.geocode_backfill --apply      # geocode and cache
    python -m portal.geocode_backfill --apply --retry-misses

This is the prerequisite for every hail join. A swath is a polygon; "which of
our customers are under it" is point-in-polygon; and until each of these rows
has a latitude and longitude the question cannot be asked at all.

Two sources, deliberately in this order:

**Canvasser pins first, because they are free and they are better.** A pin's
lat/lng came from a rep's phone while the rep was physically standing on the
property. That is a better fix than any geocoder will interpolate from a TIGER
street centerline, and it costs nothing. Seeding these first also shrinks the
batch we send.

**Then the CRM leads**, through the Census bulk endpoint.

Base44 contacts are NOT pulled here. They need a token, they are the largest
set, and they belong to the customer-identity work that decides what a customer
is in the first place — geocoding them before that lands would key thousands of
rows to an identity we are about to change.
"""
import argparse
import os
import sqlite3
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from portal import dbtune, geo  # noqa: E402


def _connect(path):
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    return dbtune.tune(conn)


def _salescrm_db():
    data_dir = os.environ.get('SALESCRM_DATA_DIR') or os.environ.get('DATA_DIR')
    if not data_dir:
        return None
    path = os.path.join(data_dir, 'salescrm.db')
    return path if os.path.exists(path) else None


def _canvasser_db():
    data_dir = os.environ.get('CANVASSER_DATA_DIR') or os.environ.get('DATA_DIR')
    if not data_dir:
        return None
    path = os.path.join(data_dir, 'canvasser.db')
    return path if os.path.exists(path) else None


def seed_from_pins(apply=False):
    """Copy pin coordinates into the cache. Returns (seeded_keys, note).

    A pin only seeds an address it actually has — most carry a reverse geocoded
    street, but a rep can save one with the field blank, and a blank key would
    collide every one of them onto a single row.

    **A dry run simulates the writes it is describing.** It tracks the keys it
    would have seeded exactly as the real run tracks the ones it did, so two
    pins on one address count once and the caller can subtract them from the
    geocoding batch. A dry run whose numbers do not match the apply is worse
    than no dry run: it is the number someone budgets against.
    """
    path = _canvasser_db()
    if not path:
        return set(), 'no canvasser.db (set CANVASSER_DATA_DIR)'
    with _connect(path) as db:
        rows = db.execute(
            "SELECT address, lat, lng FROM pins WHERE address != ''"
        ).fetchall()
    known = geo.known_keys()
    seeded = set()
    for r in rows:
        key = geo.norm_address(r['address'])
        if not key or key in known or key in seeded:
            continue
        if apply:
            geo.put(r['address'], lat=r['lat'], lng=r['lng'],
                    matched=r['address'], source='pin', status='ok', key=key)
        seeded.add(key)
    return seeded, f'{len(rows)} pins with an address'


def crm_addresses():
    """(street, city, state, zip) for every lead that has a street address."""
    path = _salescrm_db()
    if not path:
        return [], 'no salescrm.db (set SALESCRM_DATA_DIR)'
    with _connect(path) as db:
        rows = db.execute(
            "SELECT address, city, state, zip FROM leads WHERE address != ''"
        ).fetchall()
    return ([(r['address'], r['city'], r['state'], r['zip']) for r in rows],
            f'{len(rows)} leads with an address')


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument('--apply', action='store_true',
                    help='actually geocode and write; default is a dry run')
    ap.add_argument('--retry-misses', action='store_true',
                    help='re-send addresses previously cached as nomatch')
    ap.add_argument('--limit', type=int, default=0,
                    help='cap how many new addresses are sent (0 = no cap)')
    args = ap.parse_args(argv)

    print(f'cache before: {geo.counts() or "empty"}')

    seeded, pin_note = seed_from_pins(apply=args.apply)
    print(f'pins        : {pin_note} → {len(seeded)} '
          f'{"seeded" if args.apply else "would seed"}')

    addresses, crm_note = crm_addresses()
    print(f'crm         : {crm_note}')

    # `seeded` is unioned in so a dry run reports the same batch the apply
    # would send. Without it the dry run offers to pay the geocoder for an
    # address the pin seeding on the line above already covered for free.
    skip = geo.known_keys() | seeded
    if args.retry_misses:
        skip -= geo.stale_keys('nomatch')

    todo, seen = [], set()
    for addr in addresses:
        key = geo.norm_address(*addr)
        if not key or key in skip or key in seen:
            continue
        seen.add(key)
        todo.append(addr)

    if args.limit:
        todo = todo[:args.limit]

    print(f'to geocode  : {len(todo)} new address(es)')
    if not args.apply:
        for addr in todo[:10]:
            print('   ', ', '.join(p for p in addr if p))
        if len(todo) > 10:
            print(f'    ... and {len(todo) - 10} more')
        print('\ndry run — nothing written. Re-run with --apply.')
        return 0

    if todo:
        try:
            written = geo.geocode(todo)
            print(f'geocoded    : {written}')
        except Exception as exc:
            # The pin seeding above already committed, and every completed
            # chunk is already in the cache. A traceback here would hide both
            # and make a resumable job look like a total loss — re-running
            # picks up exactly where this stopped, because the cache is the
            # progress record.
            print(f'geocoded    : FAILED — {type(exc).__name__}: {exc}')
            print(f'cache after : {geo.counts()}')
            print('\nNothing was lost. The cache is the progress record, so '
                  're-running --apply resumes from here.')
            return 1
    print(f'cache after : {geo.counts()}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
