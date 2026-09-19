"""Put a name on commercial leads that are still just an LLC.

    python -m agents.b2b.sos_backfill --dry-run
    python -m agents.b2b.sos_backfill

The assessor import already asks the Colorado Secretary of State for each
owner's registered agent (sources/sos.py), but only while importing - a lead
imported before that existed, or whose entity was missed that day, stays
"JAX PROPERTIES LLC" forever. This asks again for those. Free (Socrata, no
key), cached 30 days, and fills a name only where there is none.
"""
import argparse
import sys

from .sources import sos


def run(crm, dry_run=False, log=print, lookup=None):
    lookup = lookup or sos.principals_for
    with crm.get_db() as db:
        rows = [dict(r) for r in db.execute(
            "SELECT id, company, rep FROM leads WHERE lead_type = 'commercial' "
            "AND first_name = '' AND company != '' AND dnc = 0")]
    found = lookup([r['company'] for r in rows]) if rows else {}
    filled = 0
    with crm.get_db() as db:
        for r in rows:
            who = found.get(r['company']) or {}
            if not who.get('first_name'):
                continue
            filled += 1
            if dry_run:
                continue
            db.execute("UPDATE leads SET first_name=?, last_name=?, contact_source='research', "
                       "updated_at=? WHERE id=? AND first_name=''",
                       (who['first_name'], who.get('last_name', ''), crm._now(), r['id']))
            crm._refresh_contact_quality(db, r['id'])
            crm._log_activity(db, r['id'], 'system', rep=r['rep'],
                              body=f"Registered agent per the Colorado Secretary of State: "
                                   f"{who['first_name']} {who.get('last_name', '')}".strip()
                                   + ' - often the owner of a small LLC; verify.')
    log(f'{len(rows)} commercial leads without a name; {filled} named from the SOS registry'
        + (' (dry run)' if dry_run else ''))
    return {'checked': len(rows), 'named': filled}


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split('\n\n')[0])
    ap.add_argument('--dry-run', action='store_true')
    args = ap.parse_args(argv)
    import portal.wsgi  # noqa: F401
    run(sys.modules['p1_crm_app'], dry_run=args.dry_run)
    return 0


if __name__ == '__main__':
    sys.exit(main())
