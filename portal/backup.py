"""Consistent snapshots of the three SQLite databases, as one zip.

The estimator has had a nightly backup since day one; `salescrm.db` (leads,
activities, prospecting history, documents), `canvasser.db` (pins, GPS) and
`portal.db` (every password hash and invite) have had **nothing** — they live
on the Railway volume with no copy anywhere. A dead volume, a bad migration or
a fat-fingered delete loses them outright, and pushing to GitHub does nothing
about it. This module is the missing half.

**Why not shutil.copy.** Every one of these databases runs in WAL mode (see
`portal/dbtune.py`), so the file on disk is not the database — recently
committed pages live in the `-wal` sidecar until a checkpoint folds them in.
Copying the `.db` alone can miss committed transactions; copying the three
files separately while a rep is saving a lead can catch them mid-checkpoint
and produce a snapshot that will not open. SQLite's online backup API exists
for exactly this: it takes the same locks a reader does, copies pages under
them, and restarts itself if a writer commits mid-copy. The result is always a
single consistent point in time, with no need to stop the site.

The snapshot is written as a fresh non-WAL database, so a restore is `unzip`
and go — no sidecars to keep together.
"""
import io
import json
import os
import sqlite3
import tempfile
import zipfile
from datetime import datetime, timezone

from portal import dbtune


def database_paths():
    """The three databases, resolved lazily.

    Lazily on purpose: `portal.users.db_path()` documents why freezing
    DATA_DIR into a module constant at import forces every caller to set env
    vars before the first import. Same trap, same fix.

    Mirrors each app's own resolution exactly — including the documented
    fallback to the estimator's DATA_DIR for salescrm and canvasser, so a
    misconfigured deploy gets backed up wherever the databases actually landed
    rather than silently backing up nothing.
    """
    from portal import users

    data_dir = os.environ.get('DATA_DIR')
    salescrm_dir  = os.environ.get('SALESCRM_DATA_DIR')  or data_dir
    canvasser_dir = os.environ.get('CANVASSER_DATA_DIR') or data_dir

    paths = {'portal.db': users.db_path()}
    if salescrm_dir:
        paths['salescrm.db'] = os.path.join(salescrm_dir, 'salescrm.db')
    if canvasser_dir:
        paths['canvasser.db'] = os.path.join(canvasser_dir, 'canvasser.db')
    return paths


def snapshot_bytes(path):
    """A consistent copy of the database at `path`, as bytes.

    Uses the online backup API rather than reading the file, for the WAL
    reasons in the module docstring. `busy_timeout` is applied to the source so
    a rep's in-flight write is waited out instead of raising `database is
    locked` — the same PRAGMA every other connection in the repo gets.
    """
    src = dbtune.tune(sqlite3.connect(path))
    try:
        # A named temp file, because the backup API needs a real destination
        # database rather than a buffer. delete=False + manual unlink: Windows
        # will not let sqlite3 open a path that is already held open here.
        tmp = tempfile.NamedTemporaryFile(suffix='.db', delete=False)
        tmp.close()
        try:
            dst = sqlite3.connect(tmp.name)
            try:
                src.backup(dst)
                # The destination inherits WAL from the source. Fold it back
                # into a single file so the zip holds one self-contained
                # database instead of one that expects missing sidecars.
                dst.execute('PRAGMA journal_mode = DELETE')
            finally:
                dst.close()
            with open(tmp.name, 'rb') as f:
                return f.read()
        finally:
            for suffix in ('', '-wal', '-shm'):
                try:
                    os.unlink(tmp.name + suffix)
                except OSError:
                    pass
    finally:
        src.close()


def _table_counts(blob):
    """Row counts per table, read back out of the snapshot itself.

    Deliberately measured on the *snapshot* rather than the live database:
    this is what a restore would actually get, so it is the number worth
    checking a restore against. Best effort — a manifest is not worth failing
    a backup over.
    """
    tmp = tempfile.NamedTemporaryFile(suffix='.db', delete=False)
    try:
        tmp.write(blob)
        tmp.close()
        conn = sqlite3.connect(tmp.name)
        try:
            names = [r[0] for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' "
                "AND name NOT LIKE 'sqlite_%' ORDER BY name")]
            return {n: conn.execute(f'SELECT COUNT(*) FROM "{n}"').fetchone()[0]
                    for n in names}
        finally:
            conn.close()
    except sqlite3.DatabaseError:
        return {}
    finally:
        try:
            os.unlink(tmp.name)
        except OSError:
            pass


def build_zip():
    """All three databases plus a manifest, as zip bytes.

    A database that does not exist yet is recorded in the manifest as missing
    rather than raising — a fresh volume legitimately has no canvasser.db
    until the first pin is dropped, and a backup that refuses to run until
    every app has been used is a backup nobody has.
    """
    stamp = datetime.now(timezone.utc)
    manifest = {
        'created_utc': stamp.isoformat(timespec='seconds'),
        'format': 'sqlite-online-backup, journal_mode=DELETE',
        'restore': 'Unzip and place each .db in its app DATA_DIR. No sidecars needed.',
        'databases': {},
    }

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, 'w', zipfile.ZIP_DEFLATED) as zf:
        for name, path in sorted(database_paths().items()):
            if not os.path.exists(path):
                manifest['databases'][name] = {'present': False, 'path': path}
                continue
            try:
                blob = snapshot_bytes(path)
            except sqlite3.DatabaseError as exc:
                # One unreadable database must not cost us the other two.
                manifest['databases'][name] = {
                    'present': True, 'path': path, 'error': str(exc)}
                continue
            zf.writestr(name, blob)
            manifest['databases'][name] = {
                'present': True,
                'path': path,
                'bytes': len(blob),
                'tables': _table_counts(blob),
            }
        zf.writestr('manifest.json', json.dumps(manifest, indent=2))

    return buf.getvalue(), manifest


def filename(stamp=None):
    stamp = stamp or datetime.now(timezone.utc)
    return f"p1_databases_{stamp.strftime('%Y-%m-%d')}.zip"


# ── Nightly off-platform copy ────────────────────────────────────────────────
# Railway's own volume backups are a Pro-plan feature, so on the current plan
# there is no platform-side copy at all. Email is not elegant, but it is the
# one channel this app already has working in production (the estimator has
# mailed a nightly estimates zip for months), it needs no new credentials, and
# it lands the data somewhere Railway cannot take with it.
#
# `send_email` is injected rather than imported: the sender lives in
# estimator/app.py, and importing that module from here would drag the whole
# estimator — and its boot-time side effects — into anything that wants a
# backup. It also keeps this testable with a stub.

# Matches the estimator's own threshold. Above it, mail providers start
# bouncing attachments, so send the link instead of losing the mail.
MAX_ATTACH_MB = 20


def _summary_rows(manifest):
    rows = []
    for name, info in sorted(manifest['databases'].items()):
        if not info.get('present'):
            detail = 'not created yet'
        elif info.get('error'):
            detail = f"could not be read — {info['error']}"
        else:
            tables = info.get('tables') or {}
            biggest = sorted(tables.items(), key=lambda kv: -kv[1])[:3]
            counts = ', '.join(f'{n}: {c}' for n, c in biggest) or 'empty'
            detail = f"{info['bytes'] / 1024:.0f} KB — {counts}"
        rows.append(
            f'<tr><td style="padding:4px 10px 4px 0;font-family:monospace;'
            f'font-size:12px;color:#374151">{name}</td>'
            f'<td style="padding:4px 0;font-size:12px;color:#6b7280">{detail}</td></tr>')
    return ''.join(rows)


def nightly_email(send_email, to_addr, base_url=''):
    """Build the zip and mail it. Returns True when the send was attempted.

    The row counts go in the body on purpose. A backup nobody reads is a
    backup nobody notices has been silently empty for a month — seeing
    "leads: 0" in an inbox is what catches that.
    """
    if not to_addr:
        return False

    data, manifest = build_zip()
    size_mb = len(data) / 1048576
    name = filename()

    if size_mb > MAX_ATTACH_MB:
        attachments = None
        extra = (f'<p style="font-size:13px;color:#b45309">The zip is '
                 f'{size_mb:.1f} MB — too large to attach. Download it from '
                 f'<a href="{base_url}/api/backup/databases">/api/backup/databases</a> '
                 f'(admin sign-in required).</p>')
    else:
        attachments = [(name, data)]
        extra = ''

    html = f'''<!DOCTYPE html><html><head><meta charset="UTF-8"></head>
<body style="font-family:system-ui,-apple-system,sans-serif;background:#f3f4f6;margin:0;padding:24px">
<div style="max-width:520px;margin:0 auto;background:#fff;border-radius:8px;overflow:hidden;box-shadow:0 2px 8px rgba(0,0,0,.1)">
  <div style="background:#1a3a5c;padding:20px 26px;color:#fff">
    <div style="font-size:10px;font-weight:700;letter-spacing:2.5px;text-transform:uppercase;opacity:.8;margin-bottom:6px">Project One Roofing</div>
    <h1 style="margin:0;font-size:19px;font-weight:800">&#128190; Nightly Database Backup</h1>
  </div>
  <div style="padding:20px 26px">
    <p style="font-size:13px;color:#374151;line-height:1.6;margin:0 0 12px">
      Attached is tonight&rsquo;s snapshot of the CRM, the canvasser and the
      account store ({size_mb:.1f} MB zipped).</p>
    <table style="border-collapse:collapse;margin:0 0 12px">{_summary_rows(manifest)}</table>
    {extra}
    <p style="font-size:11px;color:#9ca3af;margin:14px 0 0">
      Unzip and drop each .db back into its data directory to restore — no
      other files needed. If a row count above looks wrong, say so before the
      next one overwrites your sense of normal.</p>
  </div>
</div>
</body></html>'''

    send_email(f'💾 Database backup — {name}', html, to_addr,
               attachments=attachments)
    return True
