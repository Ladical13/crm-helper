"""One SQLite connection setup, shared by every app in the repo that opens one.

Four separate databases (portal.db, salescrm.db, canvasser.db, and Nimbus's
cache DB) were each connecting with Python's stock defaults, which are tuned
for a single-process script rather than a web app. Two PRAGMAs fix that, and
both are load-bearing the moment the site serves more than one request at a
time:

**journal_mode=WAL.** In the default `delete` journal mode a writer takes an
exclusive lock on the whole file, so one rep saving a lead blocks every reader
until the commit lands. WAL lets readers keep reading against the last
committed snapshot while a writer appends — which is the difference between
"the CRM feels slow" and "the CRM returns 500s" once gunicorn runs threads.
The setting is persistent (it lives in the database header), so this is really
a one-time migration that we re-assert on every connect because it is cheap
and makes a fresh volume correct without a migration step.

**busy_timeout.** Python's sqlite3 defaults to a 5-second timeout, but only on
the connect() call — and it raises `sqlite3.OperationalError: database is
locked` immediately rather than waiting when a *statement* meets a lock held
by another connection. Setting the PRAGMA makes every statement wait out a
brief contending write instead of failing the rep's request.

Order matters: busy_timeout is set first, so the journal_mode switch itself
waits for a contending lock rather than raising on a busy database.

`synchronous` is deliberately left at the default (FULL). WAL's usual
companion is NORMAL, which is durable against a process crash and only risks
the last few transactions on power loss — but the write volume here is a few
dozen leads and estimates a day, so there is no throughput problem worth
trading signed-contract durability for.
"""
import sqlite3

# Long enough to ride out any write this app makes (all of them are single-row
# statements against small tables), short enough that a genuinely stuck lock
# surfaces as an error instead of hanging the worker for a minute.
BUSY_TIMEOUT_MS = 5000


def tune(conn):
    """Apply the shared PRAGMAs to a fresh connection. Returns the connection.

    Safe to call on any connection, including `:memory:` databases (which
    cannot do WAL and report journal_mode 'memory' instead — not an error).
    A filesystem that refuses WAL degrades to the previous behaviour rather
    than failing the request, because a slow CRM beats a down one.
    """
    conn.execute(f'PRAGMA busy_timeout = {BUSY_TIMEOUT_MS}')
    try:
        conn.execute('PRAGMA journal_mode = WAL')
    except sqlite3.DatabaseError:
        pass
    return conn


def journal_mode(conn):
    """The connection's current journal mode, lowercased — for tests."""
    return conn.execute('PRAGMA journal_mode').fetchone()[0].lower()
