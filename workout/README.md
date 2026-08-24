# P1 Lift

A workout tracker. Log the set, see what you did last week, keep your records.
Flask + SQLite + PWA, no build step, no framework, no third-party runtime
dependency — it comes up in a basement with no signal.

![the log](static/icon-192.png)

## Run it

```bash
pip install -r requirements.txt
python app.py                 # http://127.0.0.1:5020
pytest                        # 55 tests, offline
```

With no `WORKOUT_PASSWORD` set it opens straight into the app, which is what
you want on a laptop and never what you want on the internet — see below.

## Deploy it

It is a single self-contained app: one folder, two dependencies, a `Procfile`.
Any host that runs Python will serve it.

**On Railway** (what it was built for):

1. New service → deploy from this repo → set the service's **root directory**
   to `workout`. Do not point it at the repo root; that is a different app.
2. Attach a **volume** and mount it at, say, `/data`.
3. Set the variables below.

| Variable | Required | What it does |
|---|---|---|
| `WORKOUT_PASSWORD` | **yes** | The one password that opens the app. Without it the app refuses to serve rather than publish your training log. |
| `WORKOUT_DATA_DIR` | **yes** | Where `workout.db` lives. Must be **on the volume** (`/data`), or every workout is lost on the next deploy. |
| `WORKOUT_SESSION_SECRET` | **yes** | Signs the session cookie. Unset means each worker signs differently and nothing stays signed in. Any long random string. |
| `WORKOUT_USER` | no | Name rows are stored under. Default `me`. |
| `WORKOUT_COOKIE_SECURE` | no | Forces the Secure cookie flag on or off. Derived from the environment otherwise. |

Then open the URL, sign in, and **Add to Home Screen** — it installs as *Lift*
with its own icon and opens straight into the log.

## What it does

- **A session** — add a movement, log weight × reps × RPE, warm-ups kept
  separate, a rest timer, live volume and elapsed time.
- **Last time, under every movement** — `Last (Mon Aug 17): 245×5, 245×5, 245×5`.
  You cannot progressively overload what you cannot remember.
- **History** — every session, and any of them saved as a routine to run again.
- **Records** — best estimated 1RM, heaviest weight, best set, week streak, and
  twelve weeks of volume.
- Works offline once loaded. Installs as a PWA.

## The rules it keeps

Every one of these is a way a tracker quietly lies while the number on screen
still looks plausible. All of them are pinned by tests.

- **The date comes from your browser.** A 7pm Sunday session is Monday in UTC;
  deriving the date on the server moves half your evening workouts into next
  week.
- **Warm-ups and unfilled sets never count.** Finishing deletes the empty ones,
  so history shows the session that happened, not the one you planned.
- **Records are computed on read.** Correcting a fat-fingered `2255` takes its
  bogus PR with it — a stored PR would outlive the set behind it.
- **e1RM is Epley, except a single, which is its own max.** Epley inflates a
  true 1RM by 3.3%, which would make every heavy single a PR on the spot.
- **The streak counts weeks, not days.** A daily streak makes a rest day look
  like a failure, which is a tracker arguing with your training plan.
- **The weekly chart is gap-filled.** A missed week draws as a missed week.
- **lb/kg is a label, not a conversion.** Weights are stored exactly as typed;
  flipping the unit never rewrites what you actually lifted.

## Layout

```
app.py           routes, schema, all the maths
auth.py          one password, throttled; cookie and security headers
static/          index.html, app.js, style.css, sw.js, icons
tests/           55 tests, no network, no fixtures to keep in sync
```

Nothing here imports anything from outside this folder;
`tests/test_standalone.py` fails if that ever changes.
