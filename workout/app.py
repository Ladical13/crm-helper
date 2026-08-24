"""P1 Lift — a workout tracker. Its own app, start to finish.

Flask + SQLite + PWA, deployed on its own, with its own login, its own cookie
and its own database. **It imports nothing from this repo outside this
directory** — no portal, no shared session, no shared user store — so the whole
app is `workout/` and moving it to a repo of its own is a copy of one folder.
`tests/test_standalone.py` fails if an outside import ever creeps back in.

    python app.py                    # http://127.0.0.1:5020
    pytest                           # the suite
    gunicorn app:app                 # what the Procfile runs

**Login is one password**, `WORKOUT_PASSWORD` (see auth.py). There is no signup
and no user table: a personal training log has exactly one person who should
see it. With the variable unset the app **refuses to serve in production** —
an unset variable must never be the difference between a login and an open
door — while a laptop with nothing configured opens straight into the app.

Storage is `WORKOUT_DATA_DIR/workout.db`. On a host with a mounted volume that
variable has to point INTO the volume: everywhere else is rebuilt on deploy,
and the app would work perfectly while forgetting every workout each time it
ships. It says so loudly at startup if it lands somewhere ephemeral.

The front end derives its own prefix from the URL and uses relative asset
paths, so it serves correctly at the root and would still work if this were
ever mounted under a prefix.
"""
import os
from datetime import datetime, timedelta, timezone
from functools import wraps

import sqlite3
from flask import Flask, jsonify, redirect, request, send_from_directory, session
from werkzeug.middleware.proxy_fix import ProxyFix

import auth                             # this directory; nothing above it

app = Flask(__name__, static_folder='static')
# Behind a platform proxy that terminates TLS. Without this Flask believes every
# request is http:// and marks the Secure cookie unsendable, so signing in
# appears to do nothing at all.
app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1, x_port=1)
auth.configure(app)
# A body-weight set is a few dozen bytes. This only has to stop a runaway
# client from buffering something silly into a worker — there are not many.
app.config['MAX_CONTENT_LENGTH'] = 1 * 1024 * 1024

HERE       = os.path.dirname(os.path.abspath(__file__))
STATIC_DIR = os.path.join(HERE, 'static')
# Its own variable and nothing else's. Beside the code is the right default on
# a laptop and the wrong one on a host, where the container filesystem is
# rebuilt on every deploy — hence the warning rather than a silent guess.
DATA_DIR   = os.environ.get('WORKOUT_DATA_DIR') or HERE
DB_PATH    = os.path.join(DATA_DIR, 'workout.db')

if DATA_DIR == HERE and auth.in_production():
    # Loud, because the failure is invisible: the app works perfectly and
    # forgets everything on the next deploy.
    print('[workout] WARNING: writing workout.db beside the code — set '
          'WORKOUT_DATA_DIR to a path on the mounted volume or every workout '
          'is lost the next time this deploys.')

SET_TYPES = ('work', 'warmup')


# ── Database ─────────────────────────────────────────────────────────────────

# Long enough to ride out any write this app makes (all of them are single-row
# statements against small tables), short enough that a genuinely stuck lock
# surfaces as an error instead of hanging a worker for a minute.
BUSY_TIMEOUT_MS = 5000


def get_db():
    """A tuned connection.

    Two PRAGMAs, both load-bearing the moment more than one request is in
    flight. **WAL**: in the default journal mode a writer locks the whole file,
    so saving a set blocks every read; WAL lets a reader carry on against the
    last committed snapshot. **busy_timeout**: Python's sqlite3 applies its
    timeout to connect() only and raises `database is locked` immediately when
    a *statement* meets a lock, so without this a phone and a laptop hitting the
    app at once produce a 500 rather than a wait.

    Order matters: the timeout is set first, so the WAL switch itself waits out
    a contending lock instead of raising on a busy database.
    """
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute(f'PRAGMA busy_timeout = {BUSY_TIMEOUT_MS}')
    try:
        conn.execute('PRAGMA journal_mode = WAL')
    except sqlite3.DatabaseError:
        # A filesystem that refuses WAL degrades to the old behaviour rather
        # than failing the request.
        pass
    return conn


SCHEMA = '''
    CREATE TABLE IF NOT EXISTS exercises (
        id         INTEGER PRIMARY KEY AUTOINCREMENT,
        name       TEXT NOT NULL,
        muscle     TEXT DEFAULT '',
        equipment  TEXT DEFAULT '',
        is_custom  INTEGER DEFAULT 0,
        -- '' is the shared seed library everybody sees; anything else is one
        -- person's own movement, visible only to them. A custom lift is not
        -- private data, but a library that fills up with other people's
        -- inventions is a library nobody can find their own lift in.
        owner      TEXT DEFAULT '',
        created_at TEXT NOT NULL
    );
    -- NOCASE so "Bench Press" and "bench press" cannot both exist for one
    -- person. Two spellings of one lift split its history in half, which
    -- quietly breaks the only number this app exists to show: what you did
    -- last time. Scoped by owner, so two people may each have their own.
    CREATE UNIQUE INDEX IF NOT EXISTS exercises_name_idx
        ON exercises(owner, name COLLATE NOCASE);

    -- Hiding a movement is per-person, and a row here rather than a flag on
    -- the exercise: the seed library is shared, so a flag would let one person
    -- delete "Back Squat" out of everybody else's list.
    CREATE TABLE IF NOT EXISTS exercise_hidden (
        user        TEXT NOT NULL,
        exercise_id INTEGER NOT NULL,
        PRIMARY KEY (user, exercise_id)
    );

    CREATE TABLE IF NOT EXISTS workouts (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        -- Whose session this is. Every read filters on it; nothing in this app
        -- ever returns a row belonging to somebody else.
        user        TEXT NOT NULL DEFAULT '',
        name        TEXT DEFAULT '',
        started_at  TEXT NOT NULL,
        finished_at TEXT DEFAULT '',
        -- The calendar date the lifter would call it, in THEIR timezone, sent
        -- by the browser. Streaks and "this week" are human questions: a 7pm
        -- Sunday session in Colorado is Monday in UTC, and deriving the date
        -- from started_at would move half the evening workouts into next week.
        local_date  TEXT NOT NULL,
        notes       TEXT DEFAULT '',
        created_at  TEXT NOT NULL
    );
    CREATE INDEX IF NOT EXISTS workouts_date_idx
        ON workouts(user, local_date DESC);

    CREATE TABLE IF NOT EXISTS sets (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        workout_id  INTEGER NOT NULL,
        exercise_id INTEGER NOT NULL,
        weight      REAL DEFAULT 0,
        reps        INTEGER DEFAULT 0,
        rpe         REAL DEFAULT 0,
        set_type    TEXT DEFAULT 'work',
        position    INTEGER DEFAULT 0,
        created_at  TEXT NOT NULL
    );
    CREATE INDEX IF NOT EXISTS sets_workout_idx  ON sets(workout_id);
    -- The history lookup ("what did I do last time?") runs on every exercise
    -- the moment it is added to a session, so it gets its own index rather
    -- than scanning every set ever logged.
    CREATE INDEX IF NOT EXISTS sets_exercise_idx ON sets(exercise_id, workout_id);

    CREATE TABLE IF NOT EXISTS routines (
        id         INTEGER PRIMARY KEY AUTOINCREMENT,
        user       TEXT NOT NULL DEFAULT '',
        name       TEXT NOT NULL,
        created_at TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS routine_items (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        routine_id  INTEGER NOT NULL,
        exercise_id INTEGER NOT NULL,
        position    INTEGER DEFAULT 0
    );
    CREATE INDEX IF NOT EXISTS routine_items_idx ON routine_items(routine_id, position);

    CREATE TABLE IF NOT EXISTS settings (
        user  TEXT NOT NULL,
        key   TEXT NOT NULL,
        value TEXT NOT NULL,
        PRIMARY KEY (user, key)
    );
'''

# Seeded once into an empty library. Weight is whatever the lifter types, so
# nothing here carries a number — this is a list of names, so that logging a
# set is picking from a list rather than typing "Romanian Deadlift" on a phone
# between sets.
SEED_EXERCISES = [
    ('Back Squat',           'legs',      'barbell'),
    ('Front Squat',          'legs',      'barbell'),
    ('Bulgarian Split Squat','legs',      'dumbbell'),
    ('Leg Press',            'legs',      'machine'),
    ('Leg Extension',        'legs',      'machine'),
    ('Leg Curl',             'legs',      'machine'),
    ('Walking Lunge',        'legs',      'dumbbell'),
    ('Calf Raise',           'legs',      'machine'),
    ('Deadlift',             'back',      'barbell'),
    ('Romanian Deadlift',    'back',      'barbell'),
    ('Trap Bar Deadlift',    'back',      'barbell'),
    ('Barbell Row',          'back',      'barbell'),
    ('Dumbbell Row',         'back',      'dumbbell'),
    ('Chest Supported Row',  'back',      'machine'),
    ('Lat Pulldown',         'back',      'cable'),
    ('Pull-Up',              'back',      'bodyweight'),
    ('Chin-Up',              'back',      'bodyweight'),
    ('Face Pull',            'back',      'cable'),
    ('Bench Press',          'chest',     'barbell'),
    ('Incline Bench Press',  'chest',     'barbell'),
    ('Dumbbell Bench Press', 'chest',     'dumbbell'),
    ('Incline Dumbbell Press','chest',    'dumbbell'),
    ('Chest Fly',            'chest',     'cable'),
    ('Push-Up',              'chest',     'bodyweight'),
    ('Dip',                  'chest',     'bodyweight'),
    ('Overhead Press',       'shoulders', 'barbell'),
    ('Seated Dumbbell Press','shoulders', 'dumbbell'),
    ('Lateral Raise',        'shoulders', 'dumbbell'),
    ('Rear Delt Fly',        'shoulders', 'dumbbell'),
    ('Barbell Curl',         'arms',      'barbell'),
    ('Dumbbell Curl',        'arms',      'dumbbell'),
    ('Hammer Curl',          'arms',      'dumbbell'),
    ('Preacher Curl',        'arms',      'machine'),
    ('Tricep Pushdown',      'arms',      'cable'),
    ('Skullcrusher',         'arms',      'barbell'),
    ('Overhead Tricep Ext',  'arms',      'dumbbell'),
    ('Plank',                'core',      'bodyweight'),
    ('Hanging Leg Raise',    'core',      'bodyweight'),
    ('Cable Crunch',         'core',      'cable'),
    ('Ab Wheel',             'core',      'bodyweight'),
    ('Back Extension',       'core',      'bodyweight'),
    ('Farmer Carry',         'core',      'dumbbell'),
    ('Kettlebell Swing',     'legs',      'kettlebell'),
    ('Hip Thrust',           'legs',      'barbell'),
    ('Rowing Machine',       'cardio',    'machine'),
    ('Assault Bike',         'cardio',    'machine'),
    ('Treadmill',            'cardio',    'machine'),
    ('Jump Rope',            'cardio',    'bodyweight'),
]


def init_db():
    os.makedirs(DATA_DIR, exist_ok=True)
    with get_db() as db:
        db.executescript(SCHEMA)
        # Seeded only into an empty library, never merged into a live one: a
        # deleted exercise must stay deleted, or every restart resurrects the
        # machine that is no longer in the gym.
        # `CREATE TABLE IF NOT EXISTS` only ever describes a fresh database, so
        # a column added later has to be applied explicitly or it exists on one
        # laptop and nowhere else.
        for table, column, ddl in (
                ('workouts',  'user',  "ALTER TABLE workouts ADD COLUMN user TEXT NOT NULL DEFAULT ''"),
                ('routines',  'user',  "ALTER TABLE routines ADD COLUMN user TEXT NOT NULL DEFAULT ''"),
                ('exercises', 'owner', "ALTER TABLE exercises ADD COLUMN owner TEXT DEFAULT ''")):
            cols = [r['name'] for r in db.execute(f'PRAGMA table_info({table})')]
            if column not in cols:
                db.execute(ddl)

        auth.init_throttle(db)

        if not db.execute("SELECT 1 FROM exercises WHERE owner='' LIMIT 1").fetchone():
            db.executemany(
                'INSERT INTO exercises (name, muscle, equipment, is_custom, '
                "owner, created_at) VALUES (?,?,?,0,'',?)",
                [(n, m, e, _now()) for n, m, e in SEED_EXERCISES])


# ── Helpers ──────────────────────────────────────────────────────────────────

def _now():
    return datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')


def _today():
    return datetime.now(timezone.utc).strftime('%Y-%m-%d')


# Called here rather than beside its definition: init_db seeds rows stamped
# with _now(), which is defined just above.
init_db()


def _num(value, default=0.0):
    """Parse a number the way the front end sends it — blank means zero.

    Deliberately NOT `float(v or 0)`: the string 'e' and the empty string both
    have to land on the default rather than raising, because these come
    straight off a numeric keypad on a phone with sweaty hands.
    """
    try:
        n = float(value)
    except (TypeError, ValueError):
        return default
    # NaN and inf serialize to invalid JSON and poison every total downstream.
    if n != n or n in (float('inf'), float('-inf')):
        return default
    return n


def e1rm(weight, reps):
    """Estimated one-rep max, Epley: w x (1 + reps/30).

    A single is its own e1RM — Epley would inflate a true 1RM by 3.3%, which
    makes every heavy single look like a PR the moment it is logged. Zero reps
    (a set entered but not performed) is not an attempt, so it is worth 0.
    """
    weight, reps = _num(weight), int(_num(reps))
    if reps <= 0 or weight <= 0:
        return 0.0
    if reps == 1:
        return round(weight, 1)
    return round(weight * (1 + reps / 30.0), 1)


def _json():
    return request.get_json(silent=True) or {}


def json_error(message, code=400):
    return jsonify({'error': message}), code


def current_user():
    """The signed-in owner, or None. Identity lives in auth.py."""
    return auth.current_user(get_db)


def with_db(f):
    """Require a signed-in owner, open one connection, commit on the way out.

    The user is passed to the handler rather than read inside it, so no query
    can be written that forgets to scope itself — the argument is right there
    in the signature. Every row this app stores carries a `user`, so a second
    lifter could be added without a migration.
    """
    @wraps(f)
    def wrapper(*args, **kwargs):
        if auth.misconfigured():
            return jsonify({'error': 'WORKOUT_PASSWORD is not set'}), 503
        user = current_user()
        if not user:
            return jsonify({'error': 'authentication required'}), 401
        db = get_db()
        try:
            result = f(db, user, *args, **kwargs)
            db.commit()
            return result
        finally:
            db.close()
    return wrapper


def _set_row(row):
    d = dict(row)
    d['volume'] = round(_num(d['weight']) * int(_num(d['reps'])), 1)
    d['e1rm'] = e1rm(d['weight'], d['reps'])
    return d


def _workout_totals(db, workout_id):
    """Volume, set count and exercise count for one workout.

    Warm-ups are excluded from every total. They are logged so the next session
    can repeat the ramp, but counting them inflates volume by whatever the
    lifter felt like doing before the real work — which makes week-over-week
    volume a measure of how much warming up happened.
    """
    # `reps > 0` is what separates a set that was performed from one that is
    # only on screen — the skeleton a routine lays down, or a row added a
    # moment before it gets filled in. Counting those inflates every total the
    # app shows while the session is still open, which is exactly when the
    # lifter is reading them.
    row = db.execute(
        'SELECT COUNT(*) AS sets, COALESCE(SUM(weight * reps), 0) AS volume, '
        'COUNT(DISTINCT exercise_id) AS exercises '
        "FROM sets WHERE workout_id=? AND set_type='work' AND reps > 0",
        (workout_id,)).fetchone()
    return {'sets': row['sets'], 'volume': round(_num(row['volume']), 1),
            'exercises': row['exercises']}


def _workout_row(db, row):
    d = dict(row)
    d.update(_workout_totals(db, d['id']))
    d['active'] = not d['finished_at']
    d['duration_min'] = _duration_min(d['started_at'], d['finished_at'])
    return d


def _duration_min(started_at, finished_at):
    if not started_at or not finished_at:
        return 0
    try:
        fmt = '%Y-%m-%dT%H:%M:%SZ'
        delta = datetime.strptime(finished_at, fmt) - datetime.strptime(started_at, fmt)
    except ValueError:
        return 0
    return max(0, int(delta.total_seconds() // 60))


# ── Static ───────────────────────────────────────────────────────────────────

@app.route('/')
def index():
    """The app shell, the login page, or the misconfiguration notice.

    app.js also redirects on a 401, which covers a session expiring while the
    app is open; serving the login here is what stops a cold load from painting
    an empty log for a moment first.
    """
    if auth.misconfigured():
        return auth.CONFIG_PAGE, 503
    if not current_user():
        return auth.login_page(action=request.script_root + '/login')
    return send_from_directory(STATIC_DIR, 'index.html')


@app.route('/login', methods=['GET', 'POST'])
def login():
    """One password, throttled per IP, constant-time compared.

    The throttle check runs BEFORE the comparison, so a locked-out caller costs
    nothing to refuse.
    """
    if auth.misconfigured():
        return auth.CONFIG_PAGE, 503
    action = request.script_root + '/login'
    if current_user():
        return redirect(request.script_root + '/')
    if request.method == 'GET':
        return auth.login_page(action=action)

    db = get_db()
    try:
        ip = auth.client_ip()
        locked = auth.locked_for(db, ip)
        if locked:
            mins = max(1, locked // 60)
            return auth.login_page(
                action=action,
                error=f'Too many attempts. Try again in {mins} min.'), 429
        if not auth.check_password(request.form.get('password', '')):
            auth.record_failure(db, ip)
            db.commit()
            return auth.login_page(action=action, error='Wrong password.'), 401
        auth.clear_failures(db, ip)
        db.commit()
    finally:
        db.close()
    auth.sign_in(session)
    return redirect(request.script_root + '/')


@app.route('/logout')
def logout():
    """Clear the cookie here, and the offline cache in the browser.

    The service worker keeps API responses so history is readable with no
    signal; without this they would outlive the session on the device. The
    cookie is already gone by the time the page runs, so a failure in that
    script costs the cache, never the sign-out.
    """
    session.clear()
    return auth.logout_page(request.script_root + '/login')


@app.route('/static/<path:path>')
def static_files(path):
    return send_from_directory(STATIC_DIR, path)


# Served from the app root, not /static/, for the same reason as the other
# three: a worker can only claim a scope at or below its own path.
@app.route('/sw.js')
def service_worker():
    resp = send_from_directory(STATIC_DIR, 'sw.js', mimetype='text/javascript')
    resp.headers['Cache-Control'] = 'no-cache'
    return resp


@app.route('/manifest.json')
def manifest():
    """Installs to a home screen as "Lift".

    `start_url` and `scope` are relative rather than '/', so the installed app
    stays inside whatever path it was installed from. At the root of its own
    domain the two are the same thing; under a prefix they are not, and
    relative is the one that keeps working.
    """
    return jsonify({
        'name': 'P1 Lift', 'short_name': 'Lift',
        'start_url': './', 'scope': './', 'display': 'standalone',
        'orientation': 'portrait',
        'background_color': '#0d1117', 'theme_color': '#0d1117',
        'icons': [
            {'src': 'static/icon-192.png', 'sizes': '192x192',
             'type': 'image/png', 'purpose': 'any'},
            {'src': 'static/icon-512.png', 'sizes': '512x512',
             'type': 'image/png', 'purpose': 'any'},
        ],
    })


@app.route('/health')
def health():
    """Public on purpose: a health check that needs a cookie is a health check
    the platform cannot run. It reports nothing about anybody's training."""
    return jsonify({'ok': True, 'app': 'workout'})


# ── Settings ─────────────────────────────────────────────────────────────────

@app.route('/api/settings', methods=['GET'])
@with_db
def get_settings(db, user):
    stored = {r['key']: r['value'] for r in
              db.execute('SELECT key, value FROM settings WHERE user=?', (user,))}
    # Defaulted on read rather than seeded on first sign-in: a row written for
    # somebody the moment they load the page is a row that outlives them.
    return jsonify({'unit': stored.get('unit', 'lb')})


@app.route('/api/settings', methods=['PATCH'])
@with_db
def patch_settings(db, user):
    """Only `unit` is settable, and it is a LABEL, not a conversion.

    Weights are stored exactly as typed. Converting stored history on a unit
    flip would rewrite what was actually lifted (and round it twice), so the
    honest behaviour is that the number stays and the suffix changes — flip it
    once when you start, not mid-log.
    """
    unit = str(_json().get('unit', '')).lower()
    if unit not in ('lb', 'kg'):
        return json_error("unit must be 'lb' or 'kg'")
    db.execute("INSERT INTO settings (user, key, value) VALUES (?, 'unit', ?) "
               'ON CONFLICT(user, key) DO UPDATE SET value=excluded.value',
               (user, unit))
    return jsonify({'unit': unit})


# ── Exercises ────────────────────────────────────────────────────────────────

@app.route('/api/exercises', methods=['GET'])
@with_db
def list_exercises(db, user):
    q = (request.args.get('q') or '').strip()
    muscle = (request.args.get('muscle') or '').strip()
    # The shared seed library plus your own movements, minus the ones you have
    # hidden. Somebody else's custom lift is never in your list, and hiding one
    # never takes it out of theirs.
    clauses = ["(owner='' OR owner=?)",
               'id NOT IN (SELECT exercise_id FROM exercise_hidden WHERE user=?)']
    params = [user, user]
    if q:
        # Escaped so a typed % or _ stays literal — the same trap the CRM's
        # pipeline search documents.
        clauses.append("name LIKE ? ESCAPE '\\'")
        params.append('%' + q.replace('\\', '\\\\')
                            .replace('%', '\\%').replace('_', '\\_') + '%')
    if muscle:
        clauses.append('muscle=?')
        params.append(muscle)
    rows = db.execute(
        'SELECT * FROM exercises WHERE ' + ' AND '.join(clauses) +
        ' ORDER BY name COLLATE NOCASE', params).fetchall()
    return jsonify([dict(r) for r in rows])


@app.route('/api/exercises', methods=['POST'])
@with_db
def create_exercise(db, user):
    name = (_json().get('name') or '').strip()
    if not name:
        return json_error('name is required')
    existing = db.execute(
        "SELECT * FROM exercises WHERE name=? COLLATE NOCASE AND "
        "(owner='' OR owner=?) ORDER BY owner DESC LIMIT 1", (name, user)).fetchone()
    if existing:
        # Returned rather than rejected, and un-hidden on the way: the front
        # end's "add exercise" is one box, and a lifter re-adding a movement
        # they retired wants that movement WITH its history, not an error.
        db.execute('DELETE FROM exercise_hidden WHERE user=? AND exercise_id=?',
                   (user, existing['id']))
        return jsonify(dict(db.execute('SELECT * FROM exercises WHERE id=?',
                                       (existing['id'],)).fetchone())), 200
    cur = db.execute(
        'INSERT INTO exercises (name, muscle, equipment, is_custom, owner, '
        'created_at) VALUES (?,?,?,1,?,?)',
        (name, (_json().get('muscle') or '').strip(),
         (_json().get('equipment') or '').strip(), user, _now()))
    return jsonify(dict(db.execute('SELECT * FROM exercises WHERE id=?',
                                   (cur.lastrowid,)).fetchone())), 201


@app.route('/api/exercises/<int:ex_id>', methods=['DELETE'])
@with_db
def archive_exercise(db, user, ex_id):
    """Hide, never delete, and only for you.

    The sets stay: deleting the exercise row would orphan every set ever logged
    against it and silently drop that volume out of history. And the row is
    written per-user, so retiring a machine your gym no longer has does not
    remove it from anybody else's list.
    """
    db.execute('INSERT OR IGNORE INTO exercise_hidden (user, exercise_id) '
               'VALUES (?,?)', (user, ex_id))
    return jsonify({'archived': True})


@app.route('/api/exercises/<int:ex_id>/history', methods=['GET'])
@with_db
def exercise_history(db, user, ex_id):
    """Every session this movement was worked, newest first.

    This is the screen the whole app is for: you cannot progressively overload
    what you cannot remember, and "what did I do last time" is the only number
    that matters standing under the bar.
    """
    limit = min(int(_num(request.args.get('limit'), 20)), 100)
    rows = db.execute(
        'SELECT s.*, w.local_date, w.started_at FROM sets s '
        'JOIN workouts w ON w.id = s.workout_id '
        'WHERE s.exercise_id=? AND w.user=? ORDER BY w.local_date DESC, '
        'w.id DESC, s.position, s.id', (ex_id, user)).fetchall()
    sessions, order = {}, []
    for row in rows:
        key = row['workout_id']
        if key not in sessions:
            sessions[key] = {'workout_id': key, 'local_date': row['local_date'],
                             'sets': [], 'volume': 0.0, 'best_e1rm': 0.0,
                             'top_weight': 0.0}
            order.append(key)
        s = _set_row(row)
        entry = sessions[key]
        entry['sets'].append(s)
        if s['set_type'] == 'work':
            entry['volume'] = round(entry['volume'] + s['volume'], 1)
            entry['best_e1rm'] = max(entry['best_e1rm'], s['e1rm'])
            entry['top_weight'] = max(entry['top_weight'], _num(s['weight']))
    return jsonify([sessions[k] for k in order[:limit]])


@app.route('/api/exercises/<int:ex_id>/last', methods=['GET'])
@with_db
def exercise_last(db, user, ex_id):
    """The previous session's work sets for this movement.

    `before` (a workout id) excludes the session in progress, so adding an
    exercise to today's workout shows LAST week's numbers rather than the empty
    set you just created.
    """
    before = int(_num(request.args.get('before'), 0))
    params = [ex_id, user]
    clause = ''
    if before:
        clause = ' AND s.workout_id != ?'
        params.append(before)
    row = db.execute(
        'SELECT s.workout_id, w.local_date FROM sets s '
        'JOIN workouts w ON w.id = s.workout_id '
        'WHERE s.exercise_id=? AND w.user=?' + clause +
        ' ORDER BY w.local_date DESC, w.id DESC LIMIT 1', params).fetchone()
    if not row:
        return jsonify(None)
    sets = db.execute(
        'SELECT * FROM sets WHERE workout_id=? AND exercise_id=? '
        'ORDER BY position, id', (row['workout_id'], ex_id)).fetchall()
    return jsonify({'workout_id': row['workout_id'],
                    'local_date': row['local_date'],
                    'sets': [_set_row(s) for s in sets]})


# ── Workouts ─────────────────────────────────────────────────────────────────

@app.route('/api/workouts', methods=['GET'])
@with_db
def list_workouts(db, user):
    limit = min(int(_num(request.args.get('limit'), 50)), 500)
    rows = db.execute(
        'SELECT * FROM workouts WHERE user=? '
        'ORDER BY local_date DESC, id DESC LIMIT ?', (user, limit)).fetchall()
    return jsonify([_workout_row(db, r) for r in rows])


@app.route('/api/workouts/active', methods=['GET'])
@with_db
def active_workout(db, user):
    """The session in progress, if any.

    The app reopens straight into it, because the alternative — a lifter who
    locked their phone between sets coming back to a "start workout" button —
    is how a session gets logged twice or not at all.
    """
    row = db.execute("SELECT * FROM workouts WHERE user=? AND finished_at='' "
                     'ORDER BY id DESC LIMIT 1', (user,)).fetchone()
    return jsonify(_workout_detail(db, user, row['id']) if row else None)


def _workout_detail(db, user, workout_id):
    # The user is part of the lookup, not a check after it: a workout that is
    # not yours reads as one that does not exist, which is the same 404 an id
    # off the end of the table gets. Nothing here ever confirms that somebody
    # else's session is out there.
    row = db.execute('SELECT * FROM workouts WHERE id=? AND user=?',
                     (workout_id, user)).fetchone()
    if not row:
        return None
    detail = _workout_row(db, row)
    rows = db.execute(
        'SELECT s.*, e.name, e.muscle, e.equipment FROM sets s '
        'JOIN exercises e ON e.id = s.exercise_id '
        'WHERE s.workout_id=? ORDER BY s.position, s.id', (workout_id,)).fetchall()
    groups, order = {}, []
    for r in rows:
        if r['exercise_id'] not in groups:
            groups[r['exercise_id']] = {
                'exercise_id': r['exercise_id'], 'name': r['name'],
                'muscle': r['muscle'], 'equipment': r['equipment'], 'sets': []}
            order.append(r['exercise_id'])
        groups[r['exercise_id']]['sets'].append(_set_row(r))
    detail['exercise_list'] = [groups[k] for k in order]
    return detail


@app.route('/api/workouts/<int:workout_id>', methods=['GET'])
@with_db
def get_workout(db, user, workout_id):
    detail = _workout_detail(db, user, workout_id)
    return jsonify(detail) if detail else json_error('no such workout', 404)


@app.route('/api/workouts', methods=['POST'])
@with_db
def create_workout(db, user):
    body = _json()
    open_row = db.execute("SELECT id FROM workouts WHERE user=? AND finished_at='' "
                          'ORDER BY id DESC LIMIT 1', (user,)).fetchone()
    if open_row:
        # One open session at a time. Two would split a single gym visit across
        # two records, and every total this app computes would then be wrong in
        # both directions at once.
        return jsonify(_workout_detail(db, user, open_row['id'])), 200
    local_date = (body.get('local_date') or '').strip() or _today()
    cur = db.execute(
        'INSERT INTO workouts (user, name, started_at, local_date, notes, '
        'created_at) VALUES (?,?,?,?,?,?)',
        (user, (body.get('name') or '').strip(), _now(), local_date,
         (body.get('notes') or '').strip(), _now()))
    workout_id = cur.lastrowid

    routine_id = int(_num(body.get('routine_id'), 0))
    if routine_id:
        # A routine seeds the exercise ORDER, not the sets. Pre-filling target
        # sets would put numbers on screen that were never lifted, and the one
        # thing a log must never do is show you a set you did not do.
        items = db.execute(
            'SELECT ri.exercise_id FROM routine_items ri '
            'JOIN routines r ON r.id = ri.routine_id '
            'WHERE ri.routine_id=? AND r.user=? ORDER BY ri.position, ri.id',
            (routine_id, user)).fetchall()
        for pos, item in enumerate(items):
            db.execute(
                'INSERT INTO sets (workout_id, exercise_id, weight, reps, rpe, '
                "set_type, position, created_at) VALUES (?,?,0,0,0,'work',?,?)",
                (workout_id, item['exercise_id'], pos * 100, _now()))
        if not (body.get('name') or '').strip():
            routine = db.execute('SELECT name FROM routines WHERE id=? AND user=?',
                                 (routine_id, user)).fetchone()
            if routine:
                db.execute('UPDATE workouts SET name=? WHERE id=?',
                           (routine['name'], workout_id))
    return jsonify(_workout_detail(db, user, workout_id)), 201


@app.route('/api/workouts/<int:workout_id>', methods=['PATCH'])
@with_db
def patch_workout(db, user, workout_id):
    row = db.execute('SELECT * FROM workouts WHERE id=? AND user=?',
                     (workout_id, user)).fetchone()
    if not row:
        return json_error('no such workout', 404)
    body = _json()
    if 'name' in body:
        db.execute('UPDATE workouts SET name=? WHERE id=?',
                   (str(body['name']).strip(), workout_id))
    if 'notes' in body:
        db.execute('UPDATE workouts SET notes=? WHERE id=?',
                   (str(body['notes']).strip(), workout_id))
    if body.get('finish'):
        # Empty sets are the skeleton a routine laid down for movements that
        # never got worked. They are dropped at finish rather than kept as
        # 0x0, so history shows the session that happened, not the one planned.
        db.execute('DELETE FROM sets WHERE workout_id=? AND reps=0 AND weight=0',
                   (workout_id,))
        db.execute('UPDATE workouts SET finished_at=? WHERE id=?',
                   (_now(), workout_id))
    if body.get('reopen'):
        db.execute("UPDATE workouts SET finished_at='' WHERE id=?", (workout_id,))
    return jsonify(_workout_detail(db, user, workout_id))


@app.route('/api/workouts/<int:workout_id>', methods=['DELETE'])
@with_db
def delete_workout(db, user, workout_id):
    if not db.execute('SELECT 1 FROM workouts WHERE id=? AND user=?',
                      (workout_id, user)).fetchone():
        return json_error('no such workout', 404)
    db.execute('DELETE FROM sets WHERE workout_id=?', (workout_id,))
    db.execute('DELETE FROM workouts WHERE id=?', (workout_id,))
    return jsonify({'deleted': True})


# ── Sets ─────────────────────────────────────────────────────────────────────

def _owned_set(db, user, set_id):
    """The set, if it belongs to one of this user's workouts. Sets are
    addressed by bare id, so this join is the only thing standing between a
    guessed number and somebody else's log."""
    return db.execute(
        'SELECT s.* FROM sets s JOIN workouts w ON w.id = s.workout_id '
        'WHERE s.id=? AND w.user=?', (set_id, user)).fetchone()


@app.route('/api/workouts/<int:workout_id>/sets', methods=['POST'])
@with_db
def add_set(db, user, workout_id):
    if not db.execute('SELECT 1 FROM workouts WHERE id=? AND user=?',
                      (workout_id, user)).fetchone():
        return json_error('no such workout', 404)
    body = _json()
    ex_id = int(_num(body.get('exercise_id'), 0))
    if not db.execute("SELECT 1 FROM exercises WHERE id=? AND (owner='' OR owner=?)",
                      (ex_id, user)).fetchone():
        return json_error('no such exercise', 400)
    set_type = body.get('set_type') if body.get('set_type') in SET_TYPES else 'work'
    # Sorted next to the exercise's existing sets rather than at the end of the
    # workout, so adding a set to the first movement after moving on to the
    # second does not scatter that movement down the page.
    last = db.execute('SELECT MAX(position) AS p FROM sets WHERE workout_id=? '
                      'AND exercise_id=?', (workout_id, ex_id)).fetchone()['p']
    if last is None:
        top = db.execute('SELECT MAX(position) AS p FROM sets WHERE workout_id=?',
                         (workout_id,)).fetchone()['p']
        position = (int(top) + 100) if top is not None else 0
    else:
        position = int(last) + 1
    cur = db.execute(
        'INSERT INTO sets (workout_id, exercise_id, weight, reps, rpe, set_type, '
        'position, created_at) VALUES (?,?,?,?,?,?,?,?)',
        (workout_id, ex_id, _num(body.get('weight')), int(_num(body.get('reps'))),
         _num(body.get('rpe')), set_type, position, _now()))
    row = db.execute('SELECT * FROM sets WHERE id=?', (cur.lastrowid,)).fetchone()
    return jsonify(_set_row(row)), 201


@app.route('/api/sets/<int:set_id>', methods=['PATCH'])
@with_db
def patch_set(db, user, set_id):
    row = _owned_set(db, user, set_id)
    if not row:
        return json_error('no such set', 404)
    body = _json()
    if 'weight' in body:
        db.execute('UPDATE sets SET weight=? WHERE id=?',
                   (_num(body['weight']), set_id))
    if 'reps' in body:
        db.execute('UPDATE sets SET reps=? WHERE id=?',
                   (int(_num(body['reps'])), set_id))
    if 'rpe' in body:
        db.execute('UPDATE sets SET rpe=? WHERE id=?', (_num(body['rpe']), set_id))
    if body.get('set_type') in SET_TYPES:
        db.execute('UPDATE sets SET set_type=? WHERE id=?',
                   (body['set_type'], set_id))
    return jsonify(_set_row(db.execute('SELECT * FROM sets WHERE id=?',
                                       (set_id,)).fetchone()))


@app.route('/api/sets/<int:set_id>', methods=['DELETE'])
@with_db
def delete_set(db, user, set_id):
    if not _owned_set(db, user, set_id):
        return json_error('no such set', 404)
    db.execute('DELETE FROM sets WHERE id=?', (set_id,))
    return jsonify({'deleted': True})


# ── Records and stats ────────────────────────────────────────────────────────

@app.route('/api/records', methods=['GET'])
@with_db
def records(db, user):
    """Best e1RM, heaviest weight and best single-set volume per movement.

    Computed on read, never stored. A stored PR has to be recomputed every time
    a set is edited or a workout deleted, and the first one that gets missed is
    a PR that never happened standing on the board forever.
    """
    rows = db.execute(
        'SELECT s.exercise_id, e.name, e.muscle, s.weight, s.reps, '
        'w.local_date FROM sets s '
        'JOIN exercises e ON e.id = s.exercise_id '
        'JOIN workouts w ON w.id = s.workout_id '
        "WHERE s.set_type='work' AND s.reps > 0 AND s.weight > 0 "
        'AND w.user=?', (user,)).fetchall()
    best = {}
    for r in rows:
        rec = best.setdefault(r['exercise_id'], {
            'exercise_id': r['exercise_id'], 'name': r['name'],
            'muscle': r['muscle'], 'best_e1rm': 0.0, 'e1rm_date': '',
            'top_weight': 0.0, 'top_weight_reps': 0, 'top_weight_date': '',
            'best_set_volume': 0.0})
        est = e1rm(r['weight'], r['reps'])
        if est > rec['best_e1rm']:
            rec['best_e1rm'], rec['e1rm_date'] = est, r['local_date']
        if _num(r['weight']) > rec['top_weight']:
            rec['top_weight'] = round(_num(r['weight']), 1)
            rec['top_weight_reps'] = int(r['reps'])
            rec['top_weight_date'] = r['local_date']
        rec['best_set_volume'] = max(
            rec['best_set_volume'], round(_num(r['weight']) * int(r['reps']), 1))
    return jsonify(sorted(best.values(), key=lambda d: -d['best_e1rm']))


def _week_start(date_str):
    """Monday of the week containing an ISO date."""
    d = datetime.strptime(date_str, '%Y-%m-%d').date()
    return (d - timedelta(days=d.weekday())).isoformat()


@app.route('/api/stats', methods=['GET'])
@with_db
def stats(db, user):
    """The dashboard numbers: this week, this month, streak, volume trend."""
    today = (request.args.get('today') or '').strip() or _today()
    try:
        _week_start(today)
    except ValueError:
        today = _today()

    rows = db.execute(
        'SELECT w.id, w.local_date, '
        "COALESCE(SUM(CASE WHEN s.set_type='work' THEN s.weight * s.reps END), 0) "
        'AS volume, '
        "COUNT(CASE WHEN s.set_type='work' AND s.reps > 0 THEN 1 END) AS sets "
        'FROM workouts w LEFT JOIN sets s ON s.workout_id = w.id '
        "WHERE w.finished_at != '' AND w.user=? "
        'GROUP BY w.id ORDER BY w.local_date', (user,)).fetchall()

    this_week = _week_start(today)
    weeks, days = {}, set()
    total_volume = 0.0
    for r in rows:
        try:
            week = _week_start(r['local_date'])
        except ValueError:
            continue
        bucket = weeks.setdefault(week, {'week': week, 'workouts': 0, 'volume': 0.0})
        bucket['workouts'] += 1
        bucket['volume'] = round(bucket['volume'] + _num(r['volume']), 1)
        total_volume += _num(r['volume'])
        days.add(r['local_date'])

    # Consecutive WEEKS with at least one session, counting back from this one.
    # Weekly rather than daily on purpose: a daily streak makes a rest day look
    # like a failure, which is how a tracker starts arguing with the training
    # plan. The current week does not break the streak until it is over — an
    # empty Monday should not read as "streak: 0".
    streak, cursor = 0, datetime.strptime(this_week, '%Y-%m-%d').date()
    if this_week not in weeks:
        cursor -= timedelta(days=7)
    while cursor.isoformat() in weeks:
        streak += 1
        cursor -= timedelta(days=7)

    # Gap-filled, and always reaching the current week. A series built only
    # from weeks that HAVE a workout draws a missed week as no bar at all —
    # the two weeks either side sit next to each other and the chart reports a
    # continuous block of training that did not happen. Same rule as the
    # estimator's monthly trends.
    series = []
    if weeks:
        cursor = datetime.strptime(min(weeks), '%Y-%m-%d').date()
        end = datetime.strptime(this_week, '%Y-%m-%d').date()
        while cursor <= end:
            key = cursor.isoformat()
            series.append(weeks.get(key, {'week': key, 'workouts': 0,
                                          'volume': 0.0}))
            cursor += timedelta(days=7)
    recent = series[-12:]
    return jsonify({
        'total_workouts': len(rows),
        'total_volume': round(total_volume, 1),
        'days_trained': len(days),
        'week_streak': streak,
        'this_week': weeks.get(this_week, {'week': this_week, 'workouts': 0,
                                           'volume': 0.0}),
        'weeks': recent,
    })


# ── Routines ─────────────────────────────────────────────────────────────────

@app.route('/api/routines', methods=['GET'])
@with_db
def list_routines(db, user):
    out = []
    for r in db.execute('SELECT * FROM routines WHERE user=? '
                        'ORDER BY name COLLATE NOCASE', (user,)):
        items = db.execute(
            'SELECT ri.exercise_id, e.name FROM routine_items ri '
            'JOIN exercises e ON e.id = ri.exercise_id '
            'WHERE ri.routine_id=? ORDER BY ri.position, ri.id', (r['id'],)
        ).fetchall()
        out.append({**dict(r), 'items': [dict(i) for i in items]})
    return jsonify(out)


@app.route('/api/routines', methods=['POST'])
@with_db
def create_routine(db, user):
    """Save a routine, either from an explicit exercise list or from a workout.

    Saving from a finished workout is the path that gets used: the routine you
    actually follow is the one you just did, and re-entering it by hand is the
    step where routines stop getting saved.
    """
    body = _json()
    name = (body.get('name') or '').strip()
    if not name:
        return json_error('name is required')
    ex_ids = [int(_num(x)) for x in (body.get('exercise_ids') or [])]
    from_workout = int(_num(body.get('from_workout_id'), 0))
    if from_workout and not ex_ids:
        ex_ids = [r['exercise_id'] for r in db.execute(
            'SELECT s.exercise_id, MIN(s.position) AS p FROM sets s '
            'JOIN workouts w ON w.id = s.workout_id '
            'WHERE s.workout_id=? AND w.user=? '
            'GROUP BY s.exercise_id ORDER BY p', (from_workout, user))]
    ex_ids = [x for x in ex_ids if x]
    # A movement you cannot see is a movement you cannot put in a routine —
    # otherwise a guessed id would seed a session with somebody else's lift.
    visible = {r['id'] for r in db.execute(
        "SELECT id FROM exercises WHERE owner='' OR owner=?", (user,))}
    ex_ids = [x for x in ex_ids if x in visible]
    if not ex_ids:
        return json_error('a routine needs at least one exercise')
    cur = db.execute('INSERT INTO routines (user, name, created_at) VALUES (?,?,?)',
                     (user, name, _now()))
    for pos, ex_id in enumerate(ex_ids):
        db.execute('INSERT INTO routine_items (routine_id, exercise_id, position) '
                   'VALUES (?,?,?)', (cur.lastrowid, ex_id, pos))
    return jsonify({'id': cur.lastrowid, 'name': name,
                    'exercise_ids': ex_ids}), 201


@app.route('/api/routines/<int:routine_id>', methods=['DELETE'])
@with_db
def delete_routine(db, user, routine_id):
    if not db.execute('SELECT 1 FROM routines WHERE id=? AND user=?',
                      (routine_id, user)).fetchone():
        return json_error('no such routine', 404)
    db.execute('DELETE FROM routine_items WHERE routine_id=?', (routine_id,))
    db.execute('DELETE FROM routines WHERE id=?', (routine_id,))
    return jsonify({'deleted': True})


if __name__ == '__main__':
    # Local dev only — in production the Procfile runs gunicorn, which binds
    # the port itself and never executes this. 127.0.0.1 by default because
    # with no WORKOUT_PASSWORD set this mode opens straight into the app.
    host = os.environ.get('WORKOUT_HOST', '127.0.0.1')
    app.run(host=host, port=int(os.environ.get('PORT', 5020)), debug=False)
