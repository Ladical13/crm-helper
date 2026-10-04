<!-- Source of truth for the Jarvis skill file references/daily-outreach.md.
     Upload changes via claude.ai → Customize → Skills; keep this copy in step. -->

# Mode G — Daily Outreach

The target is about 100 touches a day across the team. They come from two
sources, and the daily plan blends both:

| Source | Who | Who decides who's due | Cooldown lives in |
|---|---|---|---|
| **The CRM queue** (`/crm`) | Partners and prospects: realtors, HOAs, insurance agents, property managers, new homeowner leads | The CRM: cadence re-touches first, then fresh partners to top up | The CRM's activity log (7 days) |
| **The Den** (Base44) | Existing clients: people we've quoted or roofed | Jarvis, using the rules below | Brain `outreach.contacts_reached_7d` (14 days) |

**Order of work every run:** log what was sent → read the replies → build the plan → draft →
report.

---

## 1. Connect

```python
import os, requests
PORTAL = "https://project-one-estimator-production.up.railway.app"
DEN = "https://base44.app/api/apps/69320ef0c647fee442697971"
CO = "6984bb86d86d9c92d6827a17"          # Colorado location_id

s = requests.Session()
s.post(f"{PORTAL}/api/apibot/session",
       headers={"X-P1-Token": os.environ["P1_READONLY_TOKEN"]}).raise_for_status()
den = {"Authorization": f"Bearer {os.environ['BASE44_TOKEN']}"}
```

- **Portal 401** means the token is wrong. **404** means `P1_READONLY_TOKEN`
  isn't set on Railway.
- **Den 401** means `BASE44_TOKEN` has expired. Tell Luke to rotate it.
- **A connection refused by the proxy** means that host isn't on the
  environment's network allowlist. Say which host.

The portal session can read pipeline, analytics, hail and Nimbus, and can make
exactly one write: logging a touch. **Anything else returns 403. That's
deliberate. Report it and never work around it.**

---

## 2. Log what was sent (every run, first)

Drafts become touches only once they're in **Sent**. For each entry in
`memory["outreach"]["pending_sent"]`:

1. Search Gmail Sent for that subject sent to that recipient since `drafted_at`.
2. **Found:**
   - **CRM card:**
     ```python
     s.post(f"{PORTAL}/crm/api/queue/log", json={
         "lead_id": e["lead_id"], "kind": "email",
         "task_id": e.get("task_id"),     # re-touch cards only
         "ref": gmail_message_id,         # idempotent: safe to re-run
     }).raise_for_status()
     ```
     The CRM credits the rep who owns the lead, completes the cadence task
     and starts the 7-day cooldown.
   - **Den client:** add a dated line to the Contact's `notes` (`PATCH
     /entities/Contact/<id>`, appending, never overwriting), for example
     `2026-10-04 · review request emailed (Jarvis)`. Then append `{email,
     name, date, type, source: "den"}` to `outreach.contacts_reached_7d`.
     **Never change a Project's status from outreach.**
3. **Not found after 2 days:** drop it from `pending_sent` and mention it once
   ("12 drafts from Tuesday were never sent").

When Luke says "called Karen at Greystar", "texted the Parkers" or "met
with…", log it the same way, with `kind` set to `call`, `text` or `meeting`
and a short `body`. To find a CRM lead not on today's cards, use `GET
/crm/api/leads?q=<name>`. That list blanks phone and email for this login;
names and ids are there.

**Log only what was sent, never what was drafted.** A drafted touch logged as
done inflates the number and starts a cooldown on someone who never heard
from us. If a log call fails, say so. Never report it as done.

---

## 2b. Read the replies (every run, right after logging)

Sending 100 a day only matters if you know what came back. Search the inbox
for replies on threads Jarvis drafted in the last 21 days (keep the Gmail
thread id in `pending_sent` and in `outreach.sent_threads`). Classify each
reply and record it:

| The reply says | CRM partner: `PATCH /crm/api/leads/<id>/outreach-status` | Den client | Tell Luke |
|---|---|---|---|
| Interested, wants to talk, sent a referral | `interested` | note on Contact | **Right away, at the top of the next answer.** Draft the reply too. A warm reply that waits a day goes cold. |
| "Call me", "next week" | `callback` | note | In the brief, with the time they asked for |
| Talked by phone (Luke says so) | `connected` | note | — |
| "Not right now", "maybe in spring" | `nurture` | note | — |
| "Not interested" | `not_interested` | note | — |
| Bounced, wrong person, left the company | `bad_contact` | note | — |
| "Stop", "unsubscribe", "remove me" | **`dnc`** | note + add to `outreach.do_not_contact` | Once, so they know |

- **Opt-outs are not optional.** Record them on the same run you see them.
  `dnc` takes the partner out of fresh cards and cadence re-touches alike.
- **Jarvis can't set `appt_set`.** Booking happens on a calendar, by a
  person. When a reply asks for a time, draft the response with
  `suggest_time` from Google Calendar and leave the booking to Luke.
- **Keep score by template.** Count replies by `type` and draft step
  (first / follow-up / breakup) in `outreach.reply_stats`. When one source has
  fewer than 2 replies per 100 sends over two weeks, say so in the weekly
  wrap-up: that's a message problem, not a volume problem.

---

## 3. Build today's plan

Split the target using `memory["outreach"]["daily_plan"]`, for example
`{"luke": {"partners": 35, "clients": 15}, "bryan": {"partners": 50}}`. If it's
missing, ask Luke once who is working outreach today and how to split it, then
save the answer. **Don't put 100 on one person.** One person reviewing and
sending about 40 good drafts a day is realistic; 100 is not.

### 3a. Partners: the CRM queue

```python
q = s.get(f"{PORTAL}/crm/api/queue/today",
          params={"rep": rep, "target": n, "contact": "ready"}).json()
cards = q["due"] + q["new"]     # due = re-touches, new = fresh partners
```

- `contact=ready` returns only cards with usable contact details. Without it,
  you also get cards that need research first.
- The CRM has already applied Do Not Call, opt-outs and the cooldown. **Don't
  re-filter it, and never add partners from Base44 or Clay to make up the
  number.**
- **Queue running short?** Refill it through Nimbus, which imports into the
  CRM with dedupe and suppression applied:
  ```python
  s.post(f"{PORTAL}/nimbus/api/b2b/run", json={
      "rep": rep, "segments": ["realtor", "hoa"], "dry_run": True})
  ```
  Always do a dry run first and show Luke the counts. Run it for real only
  after he says go. A refill always names the rep; a run without one is
  refused.

### 3b. Existing clients: the Den

```python
import json, urllib.parse as u
f = u.quote(json.dumps({"location_id": CO}))
projects = requests.get(f"{DEN}/entities/Project?q={f}", headers=den).json()
contacts = {c["id"]: c for c in
            requests.get(f"{DEN}/entities/Contact?q={f}", headers=den).json()}
```

Join with `project["contact_id"]`. Use `updated_date` as the date the project
reached its current status; the Den has no separate close date.

**Leave a client out if any of these is true:**
- They have any project in `contracted`, `ready_for_production`,
  `roof_complete` or `collect_final_payment`. A job in production belongs to
  `customer-comms`, and a sales touch in the middle of an install feels
  tone-deaf.
- `is_red_flag_customer` is true.
- Their email is in `contacts_reached_7d` with a date in the last **14 days**.
  Clients are relationships; 14 days, not 7.
- A CRM lead with this `crm_contact_id` was touched in the last 7 days (the
  lead's `last_activity_at` on `GET /crm/api/leads?q=<name>`).
- There's no email and no phone.

**Pick one touch per client, the first that applies, in this order:**

| # | Touch | When | Voice and template |
|---|---|---|---|
| 1 | **Storm check** | A past client (any `paid_and_closed` project) whose ZIP is in `memory["storm"]["canvassing_targets"]` | "Hail came through [area] on [date]. Want us to take a free look?" This is the highest-value touch. On a storm day it goes first and can take most of the client allocation. |
| 2 | **Review request** | `paid_and_closed` 3–30 days ago, and no `review` touch for them in memory | `review-request` |
| 3 | **Roof Care Plan offer** | `paid_and_closed`, not an RCP subscriber, no `rcp` touch in 90 days | `roof-care-plan` ("every completed job gets an RCP offer") |
| 4 | **Referral ask** | `paid_and_closed` 30–120 days ago, never asked before | Short and personal: "who else on your street should we look at?" |
| 5 | **Stalled estimate** | `inspected` / `ready_to_close`, 14+ days since `updated_date` | `estimate-followup` |

Record the touch type as `storm`, `review`, `rcp`, `referral` or `estimate`.
It drives the rules above and the brief.

---

## 4. Draft (never send)

- **Email available:** create a Gmail draft (`gmail_create_draft`) in the
  sending rep's account.
  - **CRM card:** start from the card's server-rendered `draft` (`subject`,
    `body`). Personalise it with `hook`, `research_notes` and `recent_storm`,
    but keep the voice and never add banned openers ("just checking in").
  - **Den client:** write it in the matching specialist skill's voice.
- **Phone only:** put it on the rep's **call and text list** with name,
  company, phone and one line on why now. Calls and texts count the same as
  email.
- **Aim for about 40% email and 60% calls and texts.** One inbox sending 100
  cold emails a day risks the projectoneroofing.com domain landing in spam,
  and estimate and contract emails go down with it. Client touches (storm,
  referral) are often better as a call anyway.
- For every draft, append to `outreach.pending_sent`: `{source: "crm"|"den",
  lead_id | contact_id, task_id, rep, type, subject, recipient, drafted_at}`.

---

## 5. Report

```python
s.get(f"{PORTAL}/crm/api/outreach/summary", params={"rep": rep}).json()
s.get(f"{PORTAL}/crm/api/leaderboard").json()
```

Den touches don't appear on the CRM leaderboard. Count them from
`contacts_reached_7d` (today's `source: "den"` entries).

The brief gets one line, then the single biggest gap:

```
OUTREACH  Partners 61 · Clients 24 → 85/100   (Luke 37/50 · Bryan 48/50)
→ 41 drafts sitting in Gmail since 9am. Send those and we clear 100.
```

Don't recite counts without saying what to do about them.
