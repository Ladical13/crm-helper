"""Populate the storm archive from NOAA.

    python -m hail.backfill --days 30              # last 30 days
    python -m hail.backfill --season 2026          # Mar-Oct of one year
    python -m hail.backfill --from 2020-10-14 --to 2026-09-09
    python -m hail.backfill --days 1               # what a nightly cron runs

Roughly one second and one megabyte per day, so a full six-year backfill is
minutes rather than hours — but only severe season is worth pulling, which
`--season` does.

**Days already held are skipped**, including days recorded with no qualifying
hail, because `storms.record()` writes those too. That is what makes this
re-runnable and what lets a cron job simply ask for the last few days every
night without re-fetching them. `--refetch` overrides it for the one case that
matters: today's file is a rolling maximum that is still moving, so the last
day or two are worth pulling again once they have settled.
"""
import argparse
import datetime as dt
import sys
import time

from hail import grid as hgrid
from hail import ingest, storms

# Severe season. Colorado does get damaging hail either side of this, but the
# archive is cheap to widen later and a full-year backfill spends most of its
# time on days that never had a storm.
SEASON_MONTHS = (3, 4, 5, 6, 7, 8, 9, 10)


def dates_for(args):
    today = dt.date.today()
    if args.days:
        return [today - dt.timedelta(days=i) for i in range(args.days)][::-1]
    if args.season:
        start = dt.date(args.season, 1, 1)
        end = min(dt.date(args.season, 12, 31), today)
        out, d = [], start
        while d <= end:
            if d.month in SEASON_MONTHS:
                out.append(d)
            d += dt.timedelta(days=1)
        return out
    start = dt.date.fromisoformat(args.start)
    end = min(dt.date.fromisoformat(args.end), today)
    out, d = [], start
    while d <= end:
        out.append(d)
        d += dt.timedelta(days=1)
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    span = ap.add_mutually_exclusive_group(required=True)
    span.add_argument('--days', type=int, help='the last N days, ending today')
    span.add_argument('--season', type=int, metavar='YEAR',
                      help='March-October of one year')
    span.add_argument('--from', dest='start', help='YYYY-MM-DD (needs --to)')
    ap.add_argument('--to', dest='end', default=dt.date.today().isoformat())
    ap.add_argument('--refetch', action='store_true',
                    help='re-pull days already held; use for the last day or '
                         'two, whose rolling maximum may not have settled')
    ap.add_argument('--threshold', type=float, default=hgrid.DEFAULT_THRESHOLD_IN,
                    help=f'inches (default {hgrid.DEFAULT_THRESHOLD_IN})')
    ap.add_argument('--dry-run', action='store_true')
    args = ap.parse_args(argv)

    if args.start and not args.end:
        ap.error('--from needs --to')

    dates = [d for d in dates_for(args) if d >= ingest.EARLIEST]
    held = set() if args.refetch else storms.ingested_dates()
    todo = [d for d in dates if d.isoformat() not in held]

    print(f'range      : {dates[0]} .. {dates[-1]}  ({len(dates)} days)')
    print(f'already held: {len(dates) - len(todo)}')
    print(f'to fetch   : {len(todo)}')
    if args.dry_run:
        print('\ndry run — nothing fetched. Re-run without --dry-run.')
        return 0

    started, storms_found, failures = time.time(), 0, 0
    for i, d in enumerate(todo, 1):
        try:
            swath = ingest.swath_for(d, threshold_in=args.threshold)
        except Exception as exc:
            # One bad day must not end a six-year backfill. It is not recorded,
            # so the next run picks it up rather than treating it as a quiet day.
            failures += 1
            print(f'  {d}  FAILED {type(exc).__name__}: {exc}')
            continue
        storms.record(d.isoformat(), swath)
        if swath:
            storms_found += 1
            print(f'  {d}  {len(swath):5d} cells  max {swath.max_size:.2f}in')
        if i % 50 == 0:
            print(f'  ... {i}/{len(todo)}  ({time.time()-started:.0f}s)')

    print(f'\nfetched {len(todo)} days in {time.time()-started:.0f}s')
    print(f'  days with qualifying hail: {storms_found}')
    if failures:
        print(f'  failed (will retry next run): {failures}')
    print(f'  archive now holds: {len(storms.ingested_dates())} days')
    return 1 if failures else 0


if __name__ == '__main__':
    sys.exit(main())
