## Daily Outreach — working the CRM queue (paste into the Jarvis skill)

**Triggers:** "run outreach", "outreach for today", "get me to 100", "work the
queue", "log what I sent", "how many touches today", "who do I call".

The CRM (`/crm`) already decides *who* is due today: cadence re-touches first,
then fresh partners to top up to the target. It also handles Do Not Call,
opt-outs and the 7-day cooldown. **Never build an outreach list yourself from
Base44 or Clay for this. Work the queue.** Base44 is for jobs and customers;
partner outreach lives in the CRM.

### Connect (once per run)

Needs `P1_READONLY_TOKEN` in the environment and the portal host on the
network allowlist.

```python
import os, requests
BASE = "https://project-one-estimator-production.up.railway.app"
s = requests.Session()
r = s.post(f"{BASE}/api/apibot/session",
           headers={"X-P1-Token": os.environ["P1_READONLY_TOKEN"]})
r.raise_for_status()   # 401 = wrong token, 404 = token not set on Railway
```

The session can read pipeline, analytics, hail and Nimbus, and can make exactly
one write: logging a touch. Anything else returns 403. That's by design, so
don't work around it.

### 1. Pull today's queue, per rep

```python
q = s.get(f"{BASE}/crm/api/queue/today",
          params={"rep": "luke", "target": 100, "contact": "ready"}).json()
cards = q["due"] + q["new"]          # due = re-touches, new = fresh partners
# q["done_today"], q["remaining"] = the scoreboard
```

- **Split the target across the reps doing outreach.** Don't put 100 on one
  person. Ask Luke who is working the queue today if memory doesn't say.
- `contact=ready` returns only cards with usable contact details. Without it,
  you also get cards that need research first.
- Each card carries a server-rendered `draft` (`subject`, `body`), chosen by
  how many times the partner has been touched (first / follow-up / breakup).
  **Start from that draft.** Personalise with the card's `hook`,
  `research_notes` and `recent_storm`, but don't rewrite the voice and don't
  add banned openers ("just checking in").

### 2. Draft, never send

- **Card has an email:** create a Gmail draft (`gmail_create_draft`) in the
  rep's own account. Record `lead_id`, `task_id` (due cards only) and the
  draft's subject in brain memory under `outreach.pending_sent`.
- **Card has only a phone:** put it on a call/text list for the rep, with the
  `name`, `company`, `phone` and one line on why now. Calls and texts count
  toward the target the same as email.
- **Mix the channels: roughly 40% email, 60% calls and texts.** One inbox
  sending 100 cold emails a day risks the projectoneroofing.com domain landing
  in spam, and that would take estimate and contract emails down with it.

### 3. Log what actually went out

Run this on "log what I sent", at the start of every outreach run, and in the
morning brief.

1. For each entry in `outreach.pending_sent`, search Gmail **Sent** for the
   subject sent to that recipient.
2. Found → log it, using the Gmail message id as `ref`:

```python
s.post(f"{BASE}/crm/api/queue/log", json={
    "lead_id": lead_id, "kind": "email",
    "task_id": task_id,            # only for due cards; omit otherwise
    "ref": gmail_message_id,       # makes it safe to re-run
}).raise_for_status()
```

3. Not found after 2 days → drop it from pending and mention it once ("12
   drafts from Tuesday were never sent").
4. When Luke says "called Karen at Greystar" or "texted the Parkers", log it
   with `kind: "call"` or `"text"` and a short `body`. Use the queue card's
   `lead_id`. For anyone else, find the lead with
   `GET /crm/api/leads?q=<name>`. That list has phone and email blanked for
   this login; names and ids are there.

**Log only what was sent, never what was drafted.** A draft logged as a touch
inflates the number and starts a 7-day cooldown on someone who never heard
from us. If logging fails, say so. Don't report the touch as done.

The touch is credited to the rep who owns the lead, so the CRM leaderboard
counts it with no extra step.

### 4. Report the number

```python
s.get(f"{BASE}/crm/api/outreach/summary", params={"rep": "luke"}).json()
s.get(f"{BASE}/crm/api/leaderboard").json()
```

In a brief, one line per rep: `Luke 37/50 · Bryan 22/50 → 59 of 100 today`.
Then name the single biggest gap ("41 drafts waiting in Gmail since 9am").
Don't just recite the counts.
