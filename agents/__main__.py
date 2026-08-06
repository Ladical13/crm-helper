"""Command line for Nimbus.

    python -m agents territories                  # print the current per-rep config
    python -m agents b2b run --rep avery [--dry-run] [--segment church]
    python -m agents content listen               # pull weekly trending topics
    python -m agents content draft --topic-id 3   # draft posts for one ranked topic
    python -m agents spend                        # this month's Perplexity spend

By default the b2b runner writes through Nimbus's in-process import path
(via the composed WSGI application), so it exercises the exact same salescrm
importer as the dashboard. Requires PORTAL_USER + PORTAL_PASSWORD (an admin
account) unless --dry-run.
"""
import argparse
import json
import os
import sys

from . import config


def cmd_territories(args):
    territories = config.load_territories()
    print(json.dumps(territories, indent=2, sort_keys=True))
    return 0


def cmd_spend(args):
    from . import perplexity as ppx
    print(f'This month (UTC): ${ppx.month_spend_usd():.2f}')
    settings = config.load_settings()
    print(f'Cap: ${float(settings.get("monthly_spend_cap_usd", 150.0)):.2f}')
    return 0


def _build_admin_client():
    """Wrap portal.wsgi.application and sign in as PORTAL_USER."""
    user = os.environ.get('PORTAL_USER')
    pw = os.environ.get('PORTAL_PASSWORD')
    if not user or not pw:
        raise RuntimeError(
            'Set PORTAL_USER and PORTAL_PASSWORD env vars to run this outside '
            'the dashboard. Both must belong to an admin account.')
    from portal.wsgi import application
    from werkzeug.test import Client
    c = Client(application)
    r = c.post('/login', data={'username': user, 'password': pw})
    if r.status_code not in (301, 302, 303):
        raise RuntimeError(f'Login failed for {user}: HTTP {r.status_code}')
    return c


def cmd_b2b_run(args):
    from .b2b import dispatcher
    client = None if args.dry_run and args.skip_push else _build_admin_client()
    manifest = dispatcher.run(
        args.rep,
        segments=[args.segment] if args.segment else None,
        cities=[args.city] if args.city else None,
        per_city_limit=args.per_city_limit,
        enrich_top_n=args.enrich_top_n,
        dry_run=args.dry_run,
        client=client,
        model=args.model,
    )
    _summary(manifest)
    return 0


def cmd_content_listen(args):
    from .content import listen
    result = listen.run(market=args.market, n=args.n, model=args.model)
    print(f'  cost:  ${result["cost_usd"]:.4f}')
    if result.get('note'):
        print(f'  note:  {result["note"]}')
    print()
    for i, t in enumerate(result['topics'], 1):
        print(f'  [{i}] ({t.get("score", 0):>4.1f}) {t.get("topic", "")}')
        summary = t.get('summary', '') or ''
        if summary:
            print(f'          {summary[:180]}')
    return 0


def cmd_content_draft(args):
    from .content import draft
    # Fetch the topic from trending_topics by id (or pass ad-hoc).
    with config.get_cache_db() as db:
        row = db.execute('SELECT * FROM trending_topics WHERE id = ?',
                         (args.topic_id,)).fetchone()
    if not row:
        print(f'error: no trending topic with id={args.topic_id}',
              file=sys.stderr)
        return 2
    topic = {
        'topic': row['topic'],
        'summary': row['summary'],
        'citations': json.loads(row['sources'] or '[]'),
    }
    result = draft.draft_topic(topic,
                               platforms=tuple(args.platforms or draft.DEFAULT_PLATFORMS),
                               model=args.model)
    print(f'  cost:  ${result["cost_usd"]:.4f}')
    for d in result['drafts']:
        print(f'  {d.get("platform"):<10}  draft_id={d.get("draft_id", "-")}')
        if d.get('preview'):
            print(f'    {d["preview"][:200]}')
    return 0


def _summary(m):
    print(f'\nrun_id      {m.get("run_id")}')
    print(f'rep         {m.get("display_name") or m.get("rep")}')
    print(f'found       {m.get("found", 0):,}')
    print(f'enriched    {m.get("enriched", 0):,}')
    print(f'pushed      {m.get("pushed", 0):,}')
    print(f'deduped     {m.get("deduped", 0):,}')
    print(f'cost        ${m.get("cost_usd", 0.0):.4f}')
    if m.get('errors'):
        print(f'errors      {len(m["errors"])}')
        for e in m['errors'][:5]:
            print(f'   - {e.get("phase")}: {e.get("error", "")[:120]}')
    print(f'manifest    {m.get("manifest_path", "-")}')


def main(argv=None):
    p = argparse.ArgumentParser(prog='agents', description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest='cmd', required=True)

    s = sub.add_parser('territories', help='dump the per-rep config')
    s.set_defaults(func=cmd_territories)

    s = sub.add_parser('spend', help='show this month\'s Perplexity spend')
    s.set_defaults(func=cmd_spend)

    s = sub.add_parser('b2b')
    s2 = s.add_subparsers(dest='sub', required=True)
    r = s2.add_parser('run', help='run a per-rep territory pass')
    r.add_argument('--rep', required=True, help='e.g. avery, phil, derik, bryan')
    r.add_argument('--segment', help='override to run only one segment')
    r.add_argument('--city', help='override to run only one city')
    r.add_argument('--per-city-limit', type=int, default=10)
    r.add_argument('--enrich-top-n', type=int, default=None,
                   help='override the rep\'s enrich_top_n (cost knob)')
    r.add_argument('--model', help='perplexity model override (sonar | sonar-pro)')
    r.add_argument('--dry-run', action='store_true',
                   help='classify only, write nothing')
    r.add_argument('--skip-push', action='store_true',
                   help='(with --dry-run) do not sign in either')
    r.set_defaults(func=cmd_b2b_run)

    s = sub.add_parser('content')
    s2 = s.add_subparsers(dest='sub', required=True)
    r = s2.add_parser('listen', help='pull weekly trending topics')
    r.add_argument('--market', default='Colorado')
    r.add_argument('-n', type=int, default=10, help='how many topics')
    r.add_argument('--model')
    r.set_defaults(func=cmd_content_listen)
    r = s2.add_parser('draft', help='draft posts for one ranked topic')
    r.add_argument('--topic-id', type=int, required=True)
    r.add_argument('--platform', dest='platforms', action='append',
                   choices=['facebook', 'instagram', 'linkedin', 'blog'],
                   help='repeatable; default = facebook + instagram + linkedin')
    r.add_argument('--model')
    r.set_defaults(func=cmd_content_draft)

    args = p.parse_args(argv)
    return args.func(args)


if __name__ == '__main__':
    sys.exit(main())
