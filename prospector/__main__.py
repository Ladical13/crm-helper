"""Command line for the prospector.

    python -m prospector segments
    python -m prospector pull dora:hoa --limit 500 --out prospector/inbox/hoa.json
    python -m prospector push prospector/inbox/hoa.json \
        --base-url http://localhost:5010 --user luke --dry-run

Pull writes a self-describing file (it remembers its lead_type and source), so
push needs nothing restated. Always push with --dry-run first: it classifies
every row and writes nothing.
"""
import argparse
import json
import os
import sys
from datetime import datetime, timezone

from . import push as pushmod
from . import sources


def cmd_segments(args):
    print(f'{"SEGMENT":<24} {"LEAD TYPE":<18} {"COUNT":>8}  LABEL')
    print('-' * 100)
    for name, mod, meta in sources.all_segments():
        n = ''
        if args.count:
            try:
                n = f'{mod.count(name.split(":", 1)[1]):,}'
            except Exception as e:
                n = f'ERR {e.__class__.__name__}'
        flag = '' if meta.get('default') is not False else '  [off by default]'
        print(f'{name:<24} {meta["lead_type"]:<18} {n:>8}  {meta["label"]}{flag}')
        if meta.get('note'):
            print(f'{"":<52}  - {meta["note"]}')
    return 0


def cmd_pull(args):
    mod, seg, meta = sources.resolve(args.segment)
    rows = []
    for r in mod.pull(seg, limit=args.limit):
        rows.append(r)
        if args.progress and len(rows) % 500 == 0:
            print(f'  ... {len(rows):,} rows', file=sys.stderr)

    doc = {
        'source': args.segment,
        'lead_type': args.lead_type or meta['lead_type'],
        'generated_at': datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'),
        'count': len(rows),
        'rows': rows,
    }
    if args.out:
        os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
        with open(args.out, 'w', encoding='utf-8') as f:
            json.dump(doc, f, indent=1)
        with_contact = sum(1 for r in rows if r['phone'] or r['email'])
        with_address = sum(1 for r in rows if r['address'])
        print(f'{len(rows):,} rows -> {args.out}')
        print(f'  lead_type    {doc["lead_type"]}')
        print(f'  with address {with_address:,}   (callable / droppable)')
        print(f'  with contact {with_contact:,}   (phone or email)')
    else:
        json.dump(doc, sys.stdout, indent=1)
    return 0


def cmd_push(args):
    doc = pushmod.load(args.file)
    rows = doc['rows']
    lead_type = args.lead_type or doc.get('lead_type')
    if not lead_type:
        print('error: no lead_type in the file; pass --lead-type', file=sys.stderr)
        return 2

    print(f'{len(rows):,} rows from {args.file}')
    print(f'  -> {args.base_url}  as {args.user}  lead_type={lead_type}'
          f'{"  [DRY RUN]" if args.dry_run else ""}')
    try:
        sess = pushmod.sign_in(args.base_url, args.user)
        res = pushmod.push(
            rows, args.base_url, sess, lead_type=lead_type,
            source=args.source or doc.get('source', 'prospecting'),
            assign=args.assign, batch=args.batch, dry_run=args.dry_run,
            on_chunk=lambda done, total, c: print(
                f'  {done:,}/{total:,}  +{c["inserted"]} new, {c["duplicate"]} dupe, '
                f'{c["suppressed"]} suppressed, {c["invalid"]} invalid'),
        )
    except pushmod.PushError as e:
        print(f'error: {e}', file=sys.stderr)
        return 1

    c = res['counts']
    print(f'\n  inserted   {c["inserted"]:,}')
    print(f'  duplicate  {c["duplicate"]:,}')
    print(f'  suppressed {c["suppressed"]:,}')
    print(f'  invalid    {c["invalid"]:,}')
    print(f'  batches    {", ".join(res["batches"])}')
    if args.dry_run:
        print('\nDry run — nothing was written.')
    return 0


def main(argv=None):
    p = argparse.ArgumentParser(prog='prospector', description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest='cmd', required=True)

    s = sub.add_parser('segments', help='list available segments')
    s.add_argument('--count', action='store_true',
                   help='query live row counts (slower, hits the open-data API)')
    s.set_defaults(func=cmd_segments)

    s = sub.add_parser('pull', help='fetch a segment to JSON')
    s.add_argument('segment', help='e.g. dora:hoa, cdos:property_manager')
    s.add_argument('--limit', type=int, help='stop after N rows')
    s.add_argument('--out', help='write here instead of stdout')
    s.add_argument('--lead-type', help='override the segment default')
    s.add_argument('--progress', action='store_true')
    s.set_defaults(func=cmd_pull)

    s = sub.add_parser('push', help='import a pulled file into the CRM')
    s.add_argument('file')
    s.add_argument('--base-url', default=os.environ.get('PORTAL_URL', 'http://localhost:5010'))
    s.add_argument('--user', required=True, help='a manager account')
    s.add_argument('--lead-type', help='override the file')
    s.add_argument('--source', help='override the file')
    s.add_argument('--assign', default='', help="'round_robin', a username, or blank")
    s.add_argument('--batch', default='', help='batch id (default: source + timestamp)')
    s.add_argument('--dry-run', action='store_true', help='classify only, write nothing')
    s.set_defaults(func=cmd_push)

    args = p.parse_args(argv)
    try:
        return args.func(args)
    except KeyError as e:
        print(f'error: {e}', file=sys.stderr)
        return 2


if __name__ == '__main__':
    sys.exit(main())
