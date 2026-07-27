#!/usr/bin/env python3
"""Merge the three user stores into portal.db. One-time, idempotent.

    python -m portal.migrate_users              # dry run — prints, writes nothing
    python -m portal.migrate_users --apply      # actually write
    python -m portal.migrate_users --apply --force   # also overwrite existing rows

Sources, in password precedence order:

    1. estimator   DATA_DIR/users.json    dict keyed by username, 3-tier role
    2. salescrm    salescrm.db users      3-tier role + full_name
    3. canvasser   canvasser.db users     boolean is_admin only

The same person has different passwords in different apps today. Precedence is
estimator-first because it has the most users and is the app reps are in daily
— their estimator password becomes their one password, so nobody is locked out
and there is no coordinated reset day. All three stores hash with werkzeug, so
hashes move over verbatim and nothing needs re-hashing.

The join key is the lowercase username, which is already what
`estimates.salesperson`, `pins.rep`, `leads.rep`, and `rep_locations.username`
reference. No ID remapping is needed anywhere — this script only merges the
identity rows; every other table keeps pointing at the same strings.
"""
import argparse
import json
import os
import sqlite3
import sys

from portal import users

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Copied from estimator/app.py:65-71 rather than imported: importing
# estimator.app pulls in a 7,500-line module that seeds data directories and
# can kick off a Postgres migration on import. A one-time script should not
# have those side effects. team.json (the live runtime override) is preferred
# over this when it exists.
ESTIMATOR_DISPLAY_NAMES = {
    'avery': 'Avery Schroeder',
    'bryan': 'Bryan Samsel',
    'derik': 'Derik Lints',
    'luke':  'Luke Durnbaugh',
    'phil':  'Phil Hunt',
}


def _first_existing(*paths):
    for p in paths:
        if p and os.path.exists(p):
            return p
    return None


def estimator_path():
    return _first_existing(
        os.path.join(os.environ['DATA_DIR'], 'users.json') if os.environ.get('DATA_DIR') else None,
        os.path.join(REPO_ROOT, 'estimator', 'users.json'))


def canvasser_path():
    return _first_existing(
        os.path.join(os.environ['CANVASSER_DATA_DIR'], 'canvasser.db')
        if os.environ.get('CANVASSER_DATA_DIR') else None,
        os.path.join(REPO_ROOT, 'canvasser', 'canvasser.db'))


def salescrm_path():
    return _first_existing(
        os.path.join(os.environ['SALESCRM_DATA_DIR'], 'salescrm.db')
        if os.environ.get('SALESCRM_DATA_DIR') else None,
        os.path.join(REPO_ROOT, 'salescrm', '.localdata', 'salescrm.db'),
        os.path.join(REPO_ROOT, 'salescrm', 'salescrm.db'))


def team_json_names():
    """Live display-name overrides from the estimator's Team Logins panel."""
    path = _first_existing(
        os.path.join(os.environ['DATA_DIR'], 'team.json') if os.environ.get('DATA_DIR') else None,
        os.path.join(REPO_ROOT, 'estimator', 'team.json'))
    if not path:
        return {}
    try:
        with open(path, encoding='utf-8') as f:
            return {m['username']: m.get('display_name', '')
                    for m in json.load(f) if m.get('display_name')}
    except Exception:
        return {}


# ── Readers — each returns {username: {pw_hash, role, full_name, must_change}} ──

def read_estimator(path):
    """users.json is a dict keyed by username; role logic mirrors _get_role()."""
    if not path:
        return {}
    with open(path, encoding='utf-8') as f:
        raw = json.load(f)
    out = {}
    for username, rec in raw.items():
        if not rec.get('pw_hash'):
            continue  # invited but never enrolled — nothing to migrate
        role = rec.get('role')
        if role not in users.ROLES:
            # estimator/app.py:121 — is_admin, or the hardcoded 'luke' fallback
            role = 'admin' if (rec.get('is_admin') or username == 'luke') else 'rep'
        out[username.strip().lower()] = {
            'pw_hash': rec['pw_hash'],
            'role': role,
            'full_name': '',
            'must_change': bool(rec.get('must_change')),
        }
    return out


def _read_sqlite(path):
    if not path:
        return {}
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    try:
        cols = {r[1] for r in conn.execute('PRAGMA table_info(users)')}
        if not cols:
            return {}
        rows = conn.execute('SELECT * FROM users').fetchall()
    finally:
        conn.close()
    out = {}
    for r in rows:
        d = dict(r)
        role = d.get('role') if 'role' in cols else None
        if role not in users.ROLES:
            role = 'admin' if d.get('is_admin') else 'rep'
        out[d['username'].strip().lower()] = {
            'pw_hash': d['pw_hash'],
            'role': role,
            'full_name': (d.get('full_name') or '') if 'full_name' in cols else '',
            'must_change': False,
        }
    return out


read_salescrm = _read_sqlite
read_canvasser = _read_sqlite


# ── Merge ────────────────────────────────────────────────────────────────────

def merge(est, crm, canv):
    """Union on username. Returns (records, conflicts)."""
    names = team_json_names() or ESTIMATOR_DISPLAY_NAMES
    order = [('estimator', est), ('salescrm', crm), ('canvasser', canv)]
    everyone = sorted(set(est) | set(crm) | set(canv))

    records, conflicts = [], []
    for username in everyone:
        present = [(src, data[username]) for src, data in order if username in data]
        winner_src, winner = present[0]

        # Password: first source that has the user, in precedence order.
        pw_hash = winner['pw_hash']
        if len({p['pw_hash'] for _s, p in present}) > 1:
            conflicts.append((username, 'password',
                              f'differs across {", ".join(s for s, _ in present)}'
                              f' - keeping {winner_src}'))

        # Role: same precedence. Flag disagreements loudly; a silent demotion
        # locks a manager out of the Numbers tab, a silent promotion is worse.
        role = winner['role']
        roles = {s: p['role'] for s, p in present}
        if len(set(roles.values())) > 1:
            conflicts.append((username, 'role',
                              ', '.join(f'{s}={r}' for s, r in roles.items())
                              + f' - keeping {role} ({winner_src})'))

        # Display name: the estimator's curated roster beats a name someone
        # typed into a signup form, but flag it when they disagree.
        crm_name = next((p['full_name'] for _s, p in present if p['full_name']), '')
        curated = names.get(username, '')
        full_name = curated or crm_name
        if curated and crm_name and curated != crm_name:
            conflicts.append((username, 'full_name',
                              f'roster="{curated}" vs salescrm="{crm_name}"'
                              f' - keeping "{curated}"'))

        records.append({
            'username': username,
            'pw_hash': pw_hash,
            'role': role,
            'full_name': full_name,
            'email': f'{username}@{users.EMAIL_DOMAIN}',
            'must_change': winner['must_change'],
            'sources': [s for s, _ in present],
        })
    return records, conflicts


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--apply', action='store_true',
                    help='write to portal.db (default is a dry run)')
    ap.add_argument('--force', action='store_true',
                    help='overwrite users that already exist in portal.db')
    ap.add_argument('--if-empty', action='store_true',
                    help='do nothing when portal.db already has users (boot-time use)')
    args = ap.parse_args(argv)

    # Boot-time guard. The start command runs this on every deploy and every
    # restart, which without this flag would resurrect anyone an admin had
    # deleted since the cutover — users.json is a snapshot, not a live source.
    if args.if_empty and users.count():
        print(f'Portal store already has {users.count()} users; nothing to migrate.')
        return 0

    paths = {'estimator': estimator_path(), 'salescrm': salescrm_path(),
             'canvasser': canvasser_path()}
    print('Sources')
    for label, p in paths.items():
        print(f'  {label:<10} {p or "(not found - skipped)"}')

    est = read_estimator(paths['estimator'])
    crm = read_salescrm(paths['salescrm'])
    canv = read_canvasser(paths['canvasser'])
    print(f'  -> {len(est)} estimator, {len(crm)} salescrm, {len(canv)} canvasser')

    records, conflicts = merge(est, crm, canv)

    print(f'\nMerged: {len(records)} users -> {users.db_path()}\n')
    print(f'  {"username":<12} {"role":<9} {"full name":<20} {"from"}')
    print(f'  {"-"*12} {"-"*9} {"-"*20} {"-"*30}')
    for r in records:
        print(f'  {r["username"]:<12} {r["role"]:<9} {r["full_name"] or "-":<20} '
              f'{", ".join(r["sources"])}')

    if conflicts:
        print(f'\n{len(conflicts)} conflict(s) resolved by precedence - check these:')
        for username, field, note in conflicts:
            print(f'  {username:<12} {field:<10} {note}')

    if not args.apply:
        print('\nDry run. Nothing written. Re-run with --apply to migrate.')
        return 0

    existing = {u['username'] for u in users.all_users()}
    created = updated = skipped = 0
    for r in records:
        if r['username'] in existing:
            if not args.force:
                skipped += 1
                continue
            with users.get_db() as db:
                db.execute('UPDATE users SET pw_hash=?, role=?, is_admin=?, full_name=?,'
                           ' email=?, must_change=? WHERE username=?',
                           (r['pw_hash'], r['role'], 1 if r['role'] in users.ELEVATED else 0,
                            r['full_name'], r['email'], 1 if r['must_change'] else 0,
                            r['username']))
            updated += 1
        else:
            users.create(r['username'], pw_hash=r['pw_hash'], role=r['role'],
                         full_name=r['full_name'], email=r['email'],
                         must_change=r['must_change'])
            created += 1

    print(f'\nDone: {created} created, {updated} updated, {skipped} left alone'
          f'{" (use --force to overwrite)" if skipped else ""}.')
    if not any(r['role'] == 'admin' for r in records):
        print('WARNING: no admin among the migrated users - nobody can manage the team.')
    return 0


if __name__ == '__main__':
    sys.exit(main())
