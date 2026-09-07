"""Pull Base44 contacts and projects to a file, for /api/customers/import.

    python -m salescrm.den_export --out inbox/den.json          # fetch
    python -m salescrm.den_export --out inbox/den.json --limit 50

Deliberately a separate step from the import, exactly like `prospector/`:

  * the fetch needs a token and a network; the import needs neither, so the
    import stays testable and the token never has to reach a request handler.
  * a pull that dies halfway leaves a file you can inspect before it touches
    the database, instead of a half-written CRM.
  * the file is a record of what Base44 said on the day, which is the only way
    to answer "did the import do that, or was the source already wrong?"

The import is idempotent on `crm_contact_id`, so re-running either half is safe.
"""
import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

BASE = 'https://base44.app/api/apps/69320ef0c647fee442697971'


def fetch(entity, token, timeout=30):
    import requests
    r = requests.get(f'{BASE}/entities/{entity}',
                     headers={'Authorization': f'Bearer {token}'}, timeout=timeout)
    r.raise_for_status()
    body = r.json()
    return body if isinstance(body, list) else body.get('items', [])


def build(contacts, projects, location_id=''):
    """Attach each contact's projects, so one row carries the whole history.

    Scoped to the Colorado location when one is given: The Den holds two
    markets and importing Tyler/Longview into a Northern Colorado pipeline
    would put every one of them under the next Front Range hail swath.
    """
    by_contact = {}
    for p in projects:
        cid = p.get('contact_id') or p.get('converted_lead_id') or ''
        if cid:
            by_contact.setdefault(cid, []).append(p)
    out = []
    for c in contacts:
        if location_id and c.get('location_id') and c['location_id'] != location_id:
            continue
        row = dict(c)
        row['projects'] = by_contact.get(c.get('id'), [])
        out.append(row)
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument('--out', required=True, help='where to write the JSON')
    ap.add_argument('--limit', type=int, default=0, help='cap rows (0 = all)')
    ap.add_argument('--location', default=os.environ.get('CO_LOCATION_ID', ''),
                    help='only this location; blank for every market')
    args = ap.parse_args(argv)

    token = os.environ.get('BASE44_TOKEN', '').strip()
    if not token:
        print('BASE44_TOKEN is not set — nothing to pull.')
        return 1

    contacts = fetch('Contact', token)
    projects = fetch('Project', token)
    rows = build(contacts, projects, args.location)
    if args.limit:
        rows = rows[:args.limit]

    os.makedirs(os.path.dirname(os.path.abspath(args.out)) or '.', exist_ok=True)
    with open(args.out, 'w', encoding='utf-8') as fh:
        json.dump({'contacts': rows}, fh, indent=1)
    jobs = sum(len(r['projects']) for r in rows)
    print(f'{len(contacts)} contacts, {len(projects)} projects fetched')
    print(f'{len(rows)} rows -> {args.out} ({jobs} projects attached)')
    print('Next: POST the file to /api/customers/import with dry_run first.')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
