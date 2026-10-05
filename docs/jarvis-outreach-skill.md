<!-- Source of truth for the Jarvis skill file references/daily-outreach.md.
     Upload changes via claude.ai → Customize → Skills; keep this copy in step. -->

# Mode G — Daily Outreach

Luke works outreach alone and the number is **100 touches a day**. The only way
one person does that is if the list, the words and the bookkeeping are already
done when he sits down. That is Jarvis's job. Luke's job is three things: press
send, dial, and decide.

**Read `outreach-playbook.md` before writing a single email.** This file is the
mechanics; that one is how the words are chosen.

## How the day is split

| Side | What | Where Luke does it | Who prepares it |
|---|---|---|---|
| **Email** (about 40) | Cold first touches to anyone with an address, plus the email steps of a cadence | Gmail → Drafts → send | **Jarvis** writes every one |
| **Phone** (about 60) | Calls, voicemails and texts | CRM → ⚡ Outreach on his phone: script on screen, one tap to dial or open Messages, one tap for the outcome | The CRM. Jarvis reports what is waiting |

**Everyone is in ONE queue: the CRM's.** Cold partners, commercial buildings,
the Den's finished customers, its open jobs and its existing referral partners
are all CRM leads. The CRM owns who is due, the 7-day cooldown, Do Not Call,
opt-outs and the follow-up schedule. Jarvis keeps no parallel list and no
cooldown of its own. The server also decides which side a card is on, so a
card Jarvis drafts never shows up on Luke's phone the same day.

**Order of work every run:** log what was sent → read the replies → check
supply → draft today's emails → report.

---

## 1. Connect

```python
import os, requests
PORTAL = "https://project-one-estimator-production.up.railway.app"
REP = "luke"

s = requests.Session()
s.post(f"{PORTAL}/api/apibot/session",
       headers={"X-P1-Token": os.environ["P1_READONLY_TOKEN"]}).raise_for_status()
```

- **401** means the token is wrong. **404** means `P1_READONLY_TOKEN` isn't set
  on Railway.
- **A connection refused by the proxy** means the host isn't on the
  environment's network allowlist. Say which host.
- **403 on anything** is deliberate. This login reads reports, logs touches,
  records replies, imports prospects for a named rep, and runs Nimbus. Nothing
  else. Report what was refused and never look for another way in.

---

## 2. Log what was sent (every run, first)

A draft becomes a touch only once it is in **Sent**. For each entry in
`memory["outreach"]["pending_sent"]`:

1. Search Gmail Sent for that subject to that recipient since `drafted_at`.
2. **Found:**
   ```python
   s.post(f"{PORTAL}/crm/api/queue/log", json={
       "lead_id": e["lead_id"], "kind": "email",
       "outcome": "emailed",            # this is what books the follow-up
       "task_id": e.get("task_id"),     # scheduled cards only
       "ref": gmail_message_id,         # idempotent: safe to re-run
   }).raise_for_status()
   ```
   The CRM credits Luke, starts the cooldown, and **books the next touch**: a
   call in three days, or the next step of the cadence the card belongs to.
   **Always send `outcome`.** Without it the touch is logged and nothing is
   booked, and that lead comes back a week later as if nobody had written.
3. **Not found after 2 days:** drop it from `pending_sent` and say so once
   ("12 drafts from Tuesday were never sent").

**When Luke tells you about a touch** ("called Karen at Greystar, left a
voicemail"), find the lead with `GET /crm/api/leads?q=<name>` (names and ids
only; phone and email are blanked for this login) and log it the same way:

| Luke says | `kind` | `outcome` |
|---|---|---|
| called, no answer | `call` | `no_answer` |
| left a voicemail | `call` | `left_vm` |
| texted them | `text` | `texted` |
| talked to them | `call` | `talked` |
| met / dropped by | `meeting` / `door` | `talked` / `dropped_by` |
| "call me back Tuesday" | `call` | `callback` plus `follow_up_at: "2026-10-13"` |
| they're interested | `call` | `interested` |
| not right now | `call` | `not_now` |
| not interested | `call` | `not_interested` |
| wrong number | `call` | `wrong_number` |

**Jarvis cannot log an appointment** (the CRM returns 403). Booking is done by
a person on a calendar: tell Luke to tap *Appointment set* on the card.

**Log only what was sent, never what was drafted.** If a log call fails, say
so. Never report it as done.

---

## 2b. Read the replies (every run, right after logging)

Search the inbox for replies on threads Jarvis drafted in the last 21 days
(`outreach.sent_threads` holds the Gmail thread ids). Classify and record:

| The reply says | `PATCH /crm/api/leads/<id>/outreach-status` | Tell Luke |
|---|---|---|
| Interested, wants to talk, sent a referral | `interested` | **At the top of the next answer, with the reply drafted.** A warm reply that waits a day goes cold. |
| "Call me", "next week" | `callback` | In the brief, with the time they asked for |
| Talked by phone (Luke says so) | `connected` | — |
| "Not right now", "maybe in spring" | `nurture` | — |
| "Not interested" | `not_interested` | — |
| Bounced, wrong person, left the company | `bad_contact` | Count bounces in the report |
| "Stop", "unsubscribe", "remove me" | **`dnc`** | Once, so he knows |

- **Opt-outs are not optional.** Record them on the same run you see them.
- **When a reply asks for a time**, draft the response using `suggest_time`
  from Google Calendar and leave the booking to Luke.
- **Keep score.** In `outreach.reply_stats`, count sends and replies by lead
  type and by step (first / followup / breakup).
- **More than 2 bounces in a day:** stop drafting to that lead type and say
  so. Bounces are what gets a domain flagged.

---

## 3. Check supply

```python
email = s.get(f"{PORTAL}/crm/api/queue/today",
              params={"rep": REP, "channel": "email", "contact": "ready", "target": 200}).json()
phone = s.get(f"{PORTAL}/crm/api/queue/today",
              params={"rep": REP, "channel": "phone", "contact": "ready", "target": 200}).json()
need_research = s.get(f"{PORTAL}/crm/api/queue/today",
                      params={"rep": REP, "contact": "research", "target": 200}).json()
```

- `due` are scheduled touches (cadence steps and follow-ups). `new` are fresh
  cards. In `due` rows **`id` is the task id and `lead_id` is the lead**; in
  `new` rows `id` is the lead id.
- **Supply is short when** the email side has fewer than three days of fresh
  cards (under about 120), or the phone side has fewer than 60 cards in total.
  Say so in the report, with the lead types that are thin.

**Refilling, in order of cost:**

1. **Research what is already imported** (`need_research` is not empty):
   ```python
   s.post(f"{PORTAL}/nimbus/api/b2b/reenrich",
          json={"lead_type": "realtor", "limit": 75}).json()   # -> {"run_id": ...}
   s.get(f"{PORTAL}/nimbus/api/runs/{run_id}").json()          # poll until status != "running"
   ```
   About two cents a lead. Roughly one in four comes back with a named person,
   more with a general phone or email. Check `month_spend_usd` in
   `GET /nimbus/api/settings` first and report it after. **Standing limit: 75
   leads a day and $10 a week without asking. Above that, or once the month's
   spend passes $100, ask Luke.** A `dry_run` costs the same as a real run.
2. **New names** come from the repo's prospector, so only a session that has
   the repo (the Monday routine, or Luke's laptop) can do it:
   ```bash
   python -m prospector pull cdos:realty --cities noco --out /tmp/realty.json
   python -m prospector push /tmp/realty.json --base-url $PORTAL \
       --token-env P1_READONLY_TOKEN --assign luke --dry-run
   ```
   Segments: `cdos:realty`, `cdos:property_manager`, `cdos:hoa_manager`,
   `cdos:insurance_agent`, `dora:hoa`. **Always dry run first and show Luke the
   counts. Run it for real only after he says go.**
3. **The Den's customers, open jobs and partners** (`den:customers`,
   `den:open_jobs`, `den:partners`) refresh the same way and need
   `BASE44_TOKEN`. Re-running is safe: nobody is imported twice.

**Never add anyone to make up the number from Clay, Apollo, Base44 or a web
search.** If they are not in the CRM, they have not been through suppression.

---

## 4. Draft today's emails (never send)

```python
target = memory["outreach"].get("daily_plan", {}).get("email_target", 15)
q = s.get(f"{PORTAL}/crm/api/queue/today",
          params={"rep": REP, "channel": "email", "contact": "ready", "target": target}).json()
cards = q["due"] + q["new"]
```

1. **Skip any lead already in `pending_sent`.** It has a draft waiting.
2. Start from the card's server-rendered `draft` (`subject`, `body`). The body
   ends with the signature: Luke's name, the company, the postal address and
   the opt-out line. **Keep that block exactly as it is.**
3. Personalise the opening using `hook`, `research_notes`, `recent_storm`,
   `company` and `city`, by the rules in `outreach-playbook.md`. If there is
   nothing true and specific to say, send the template as written. A generic
   line pretending to be personal is worse than none.
4. **Offers.** `GET /crm/api/offers` lists them; use only `status: "live"`
   ones whose `for` includes the lead's type (or `past_customer`). The link is
   `{PORTAL}/crm/offer/<key>?r=luke`.
   - **Never in a first cold email.** No links at all in a first touch.
   - From the second touch, or in a reply, or for anyone who already knows us
     (a `due` card for a customer or an existing partner): use the offer's own
     `email_subject` and `email_body`, with the link filled in.
5. Create the Gmail draft (`create_draft`) in Luke's account.
6. Append to `outreach.pending_sent`: `{lead_id, task_id, lead_type, step,
   subject, recipient, thread_id, drafted_at}`.

**The ramp protects the domain.** `daily_plan.email_target` starts at **15**,
goes to **25** after a week with no bounces above 2 a day and no spam
complaints, then **40**. Only Luke moves it ("raise email to 25"). Never draft
more than the target, and never more than 40.

---

## 5. The phone side

Jarvis does not write the texts or scripts: every card on the Outreach tab
already has its call script, voicemail, text and offer, picked for that lead
type and that touch. What Jarvis adds:

- **The count**, from `phone` in § 3: how many scheduled, how many new, split
  customers / partners / commercial.
- **The three worth doing first**: anyone marked `interested` or `callback`,
  then customers, then partners with a `recent_storm`.
- **The link**: `{PORTAL}/crm/#outreach`.

When Luke asks "who do I call", answer from this, briefly.

---

## 6. Report

```python
day = phone.get("day") or {}     # {"target": 100, "done": 37, "email_target": 40, "email_done": 12}
```

One line, then the single biggest gap:

```
OUTREACH  37/100 today  ·  emails 12/15  ·  calls & texts 25/60  ·  streak 4 days
→ 15 drafts in Gmail since 6:15. Send those, then 23 cards on the Outreach tab.
```

- **Streak** (`outreach.streak`): consecutive weekdays that hit the day's
  target. A day under it resets the count; say so plainly, once.
- **Fridays:** replies per 100 sends by lead type and step, from
  `reply_stats`. Under 2 per 100 over two weeks is a message problem, not a
  volume problem: propose a rewrite of that template, in the playbook's terms,
  for Luke to paste into Playbook → Templates.
- Don't recite counts without saying what to do about them.

---

## Memory this mode keeps

```json
"outreach": {
  "daily_plan": {"email_target": 15, "started": "2026-10-06"},
  "pending_sent": [{"lead_id": "...", "task_id": "...", "lead_type": "realtor",
                    "step": "first", "subject": "...", "recipient": "...",
                    "thread_id": "...", "drafted_at": "..."}],
  "sent_threads": [{"thread_id": "...", "lead_id": "...", "sent_at": "..."}],
  "reply_stats": {"realtor": {"first": {"sent": 0, "replies": 0}}},
  "streak": {"days": 0, "last_hit": ""}
}
```

`contacts_reached_7d` is retired. The CRM's activity log is the cooldown.

## The scheduled runs

| When (Denver, weekdays) | What |
|---|---|
| 6:15am | **Build the day:** §§ 2, 2b, 3, 4, then one push with § 6's line |
| 12:30pm | **Reply sweep:** §§ 2, 2b only. Speak only if there is a reply that needs Luke |
| 4:49pm | **Close-out:** §§ 2, 2b, 6 |
| Friday 3pm | **Scorecard:** the Friday part of § 6 |
| Monday 5am | **Supply:** § 3 in a session with the repo. Dry runs and a report; nothing imported |
