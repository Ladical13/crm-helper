<!-- Source of truth for the Jarvis skill file references/daily-outreach.md.
     Upload changes via claude.ai → Customize → Skills; keep this copy in step. -->

# Mode G — Daily Outreach

The number is **100 touches a day, per rep**. The only way one person does that
is if the list, the words and the bookkeeping are already done when they sit
down. That is Jarvis's job. The rep's job is three things: press send, dial,
and decide.

**Read `outreach-playbook.md` before writing a single email.** This file is the
mechanics; that one is how the words are chosen.

## Who works how

| Rep | Emails | Calls and texts | Cold partners come from |
|---|---|---|---|
| `luke` | **Jarvis drafts them** into his Gmail; he sends | CRM → ⚡ Outreach on his phone | Northern Colorado (`--cities noco`) |
| `derik` | None drafted. He works every card on the Outreach tab himself, email included (one tap opens it in his own Gmail) | Same tab | Lakewood, Boulder, Broomfield, Golden, until Luke names others |

Jarvis has **Luke's** Gmail and nobody else's. For Derik it keeps his queue
stocked (the weekly refill) and reports his number. It never drafts in his
name.

**If Luke is covering Derik's list**, the CRM already knows: Derik's cards
arrive in Luke's queue, signed as Luke. Jarvis does nothing differently. It
asks for `rep=luke` and works what it is given.

## The rules that do not bend

- **Everyone is in ONE queue: the CRM's.** Cold partners, commercial buildings,
  the Den's customers, its open jobs and its partners are all CRM leads. The
  CRM owns who is due, the 7-day cooldown, Do Not Call, opt-outs and the
  follow-up schedule.
- **The CRM keeps the books too.** What is drafted, what was sent, what came
  back, the streak and the supply all live there. **Jarvis keeps no outreach
  state of its own**: nothing in brain memory, nothing in Drive. Every run
  starts by asking the CRM, so a run that fails loses nothing and a run that
  repeats breaks nothing.
- **Jarvis drafts. A person sends.** Always.
- **Log only what was sent**, never what was drafted.
- **A 403 or a 429 from the portal is an answer.** Report what was refused and
  never look for another way in.

**Order of work every run:** log what was sent → read the replies → draft
today's emails → report.

---

## 1. Connect

```python
import os, requests
PORTAL = "https://project-one-estimator-production.up.railway.app"
REP = "luke"                      # the rep this run is for
REPS = ["luke", "derik"]          # everyone the report covers

s = requests.Session()
token = os.environ.get("P1_READONLY_TOKEN", "")
s.post(f"{PORTAL}/api/apibot/session",
       headers={"X-P1-Token": token} if token else {}).raise_for_status()
```

**Having no `P1_READONLY_TOKEN` variable is normal in the cloud.** There the
token is a network secret: the environment adds it to every request to the
portal on the way out, and the session never holds it. So send no header and
let the portal's answer decide. Never look for the token, print it, or ask
for it.

- **200**: signed in.
- **401** means no valid token reached the portal. In the cloud, the
  environment's secret for this host is missing or wrong; on a laptop,
  `P1_READONLY_TOKEN` is unset or wrong. **404** means it isn't set on Railway.
- **A connection refused by the proxy** means the host isn't on the
  environment's network allowlist. Say which host.
- **If the sign-in fails:** say which of these it was in one line and stop.
  Nothing else in this mode can run without it.

---

## 2. Log what was sent (every run, first)

```python
pending = s.get(f"{PORTAL}/crm/api/queue/drafts",
                params={"rep": REP, "status": "pending"}).json()
```

Each row is a draft waiting in Gmail: `id`, `lead_id`, `task_id`, `recipient`,
`subject`, `draft_ref` (the Gmail draft id), `created_at`, `stale`.

For each one, search Gmail **Sent** for that subject to that recipient since
`created_at`.

1. **Found:**
   ```python
   s.post(f"{PORTAL}/crm/api/queue/log", json={
       "lead_id": d["lead_id"], "kind": "email",
       "outcome": "emailed",              # this is what books the follow-up
       "draft_id": d["id"],               # closes the draft, credits the sender
       "task_id": d["task_id"] or None,
       "ref": gmail_message_id,           # idempotent: safe to re-run
       "thread_ref": gmail_thread_id,     # how replies are found later
   }).raise_for_status()
   ```
   The CRM credits the rep, starts the cooldown and **books the next touch**: a
   call in three days, or the next step of the cadence the card belongs to.
   **Always send `outcome` and `draft_id`.**
2. **Not found, and `stale` is true** (two days unsent): delete the draft from
   Gmail (`delete_draft` with `draft_ref`), then
   ```python
   s.patch(f"{PORTAL}/crm/api/queue/drafts/{d['id']}", json={"status": "expired"})
   ```
   Delete first. An expired draft left in Gmail can still be sent, and its lead
   will be drafted again. Count them and say so once ("12 drafts from Tuesday
   were never sent").
3. **Not found, not stale:** leave it. It is still waiting.

**When Luke tells you about a touch** ("called Karen at Greystar, left a
voicemail"), find the lead with `GET /crm/api/leads?q=<name>` (names and ids
only; phone and email are blanked for this login) and log it:

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

If a log call fails, say so. Never report it as done.

---

## 2b. Read the replies (every run, right after logging)

```python
sent = s.get(f"{PORTAL}/crm/api/queue/drafts",
             params={"rep": REP, "status": "sent", "days": 21}).json()
```

For each row with an empty `reply`, open its Gmail thread (`thread_ref`). If
someone other than the rep has written since `sent_at`, classify it and record
it **on the draft**:

```python
s.patch(f"{PORTAL}/crm/api/queue/drafts/{d['id']}",
        json={"reply": "interested", "note": "Wants a look at the Windsor listing"})
```

| The reply says | `reply` | What the CRM does | Tell Luke |
|---|---|---|---|
| Interested, wants to talk, sent a referral | `interested` | Puts "Book the appointment" at the top of today's calls | **At the top of the next answer, with the reply drafted.** A warm reply that waits a day goes cold |
| "Call me Tuesday" | `callback` + `follow_up_at` | Books the call for that day | In the brief, with the day |
| "Not right now", "maybe in spring" | `nurture` | Stops the sequence, checks back in 90 days | — |
| "Not interested" | `not_interested` | Cancels every follow-up and closes the lead | — |
| Bounced, wrong person, left the company | `bad_contact` | Cancels follow-ups, books "find the right contact" | Count bounces in the report |
| "Stop", "unsubscribe", "remove me" | **`dnc`** | Cancels everything and suppresses the address for good | Once, so he knows |

- **One call does all of it.** Do not also set the outreach status; the reply
  does. Recording the same reply twice changes nothing.
- **An auto-reply or out-of-office is not a reply.** Record nothing.
- **Opt-outs are not optional.** Record them on the run you see them.
- **`callback` needs the day they asked for.** If they gave none, it is
  `interested`.
- **When a reply asks for a time**, draft the response using `suggest_time`
  from Google Calendar and leave the booking to Luke.
- **More than 2 bounces in a day:** stop drafting to that lead type for the
  day and say so. Bounces are what gets a domain flagged.

---

## 3. The scorecard

```python
card = s.get(f"{PORTAL}/crm/api/outreach/scorecard", params={"rep": REP}).json()
```

One read, with no contact details in it:

- `today`: `done`, `target`, `email_done`, `email_target`.
- `streak`: consecutive weekdays that hit the target. Weekends are skipped and
  today is not a miss until it is over.
- `days`: the last fortnight, newest first.
- `email`: `sent`, `replies`, `positive`, `bounces`, `optouts`, `pending`,
  `stale`, and `by_type[lead_type][step]` with `sent` and `replies`.
- `supply.fresh`: cards ready on the `email` side, the `phone` side, and how
  many still need `research`. `supply.days`: how many days of cards are left
  (`email` and `phone` for a rep with an email share; `all` for one without).

**Supply is short under 3 days on any side.** Say so in the report, with the
lead types that are thin (`supply.fresh_by_type`). That is all a daily run
does about it: **daily runs never import and never research.** Refilling is
§ 7, once a week, on Luke's word.

---

## 4. Draft today's emails (never send)

```python
q = s.get(f"{PORTAL}/crm/api/queue/today",
          params={"rep": REP, "channel": "email", "contact": "ready"}).json()
cards = q["due"] + q["new"]
```

**Do not pass `target`.** The CRM already knows the day's email share and what
has been sent today, and hands back exactly the cards to draft. Asking for
more is how a domain gets flagged. If the share is 0 the rep has no emails
drafted for them: stop here.

In `due` rows **`id` is the task id and `lead_id` is the lead**; in `new` rows
`id` is the lead id. A card whose `draft` is empty has no template for its
lead type: skip it and name the type in the report. For each card, in this
order:

1. **Reserve it.**
   ```python
   r = s.post(f"{PORTAL}/crm/api/queue/drafts", json={
       "lead_id": lead_id, "rep": REP,
       "task_id": task_id,                       # due cards only
       "subject": subject, "step": card["draft"]["step"],
       "template_id": card["draft"]["template_id"]})
   ```
   **409 means skip this card**: it already has a draft waiting, or the contact
   opted out since the queue was built. Never draft a card that was not
   reserved.
2. **Write it.** Start from the card's `draft` (`subject`, `body`). The body
   ends with the signature: the rep's name, the company, the postal address
   and the opt-out line. **Keep that block exactly as it is.** Personalise the
   opening using `hook`, `research_notes`, `recent_storm`, `company` and
   `city`, by the rules in `outreach-playbook.md`. If there is nothing true and
   specific to say, send the template as written.
3. **Create the Gmail draft**, then tell the CRM which one it is:
   ```python
   s.patch(f"{PORTAL}/crm/api/queue/drafts/{r.json()['id']}",
           json={"draft_ref": gmail_draft_id, "thread_ref": gmail_thread_id})
   ```
   If Gmail refused the draft, release the card instead:
   `json={"status": "expired"}`.

**Offers.** `GET /crm/api/offers` lists them; use only `status: "live"` ones
whose `for` includes the lead's type (or `past_customer`). The link is
`{PORTAL}/crm/offer/<key>?r=<REP>`.

- **Never in a first cold email.** No links at all in a first touch.
- From the second touch, or in a reply, or for anyone who already knows us (a
  `due` card for a customer or an existing partner): use the offer's own
  `email_subject` and `email_body`, with the link filled in.

**The ramp protects the domain.** The email share starts at **15**, goes to
**25** after a week with no day over 2 bounces and no spam complaint, then
**40**. The calls and texts are always the rest of the hundred, so the day is
100 from the first morning. **Only Luke moves it**: CRM → Outreach → Daily
plan. Jarvis recommends on Fridays and cannot change it (the CRM returns 403).

---

## 5. The phone side

Jarvis does not write the texts or scripts: every card on the Outreach tab
already has its call script, voicemail, text and offer, picked for that lead
type and that touch. What Jarvis adds:

- **The count**, from `card["today"]`.
- **The three worth doing first.** Ask for the top of the list
  (`channel=phone`, `contact=ready`, `target=15`): anyone marked `interested`
  or `callback`, then customers, then partners with a `recent_storm`.
- **The link**: `{PORTAL}/crm/#outreach`.

When Luke asks "who do I call", answer from this, briefly.

---

## 6. Report

One line for the rep the run is for, one for every other rep in `REPS` (their
own scorecard), then the single biggest gap:

```
OUTREACH  37/100 today  ·  emails 12/15  ·  calls & texts 25/85  ·  streak 4 days
DERIK     22/100 today  ·  streak 1 day  ·  6 days of cards
→ 15 drafts in Gmail since 6:15. Send those, then 60 cards on the Outreach tab.
```

- **A streak that reset:** say so plainly, once.
- **Fridays:** replies per 100 sends by lead type and step, from
  `email.by_type`. Under 2 per 100 over two weeks is a message problem, not a
  volume problem: propose a rewrite of that template, in the playbook's terms,
  for Luke to paste into Playbook → Templates. Say whether the ramp should
  move.
- Don't recite counts without saying what to do about them.

---

## 7. The weekly refill (Mondays, and only on Luke's go)

**One run a week. Research capped at $15. Nothing is imported and nothing is
spent until Luke says go.** The $15 is enforced by the portal, not by this
page: past it, `POST /nimbus/api/b2b/reenrich` returns **429** and that is the
end of research for the week.

It needs the repo (the prospector) and access to the Den, so only the Monday
routine or Luke's laptop can do it. In the cloud both tokens are network
secrets, added to requests on the way out: `P1_READONLY_TOKEN` and
`BASE44_TOKEN` will be unset there and the commands below still work as
written. A push that fails to sign in, or a Den pull that is refused, means
the environment's secret for that host is missing or wrong: say which and stop.

**Step 1: the proposal. Dry runs only.**

```bash
# The Den: who already knows us. Derik keeps the jobs he sold; the rest are Luke's.
for seg in den:customers den:open_jobs den:partners; do
  python -m prospector pull $seg --out /tmp/$seg.json
  python -m prospector push /tmp/$seg.json --base-url $PORTAL \
      --token-env P1_READONLY_TOKEN --assign luke --owners derik --dry-run
done

# Cold partners, each rep's own cities.
for seg in cdos:realty cdos:property_manager cdos:hoa_manager cdos:insurance_agent; do
  python -m prospector pull $seg --cities noco --out /tmp/luke-$seg.json
  python -m prospector push /tmp/luke-$seg.json --base-url $PORTAL \
      --token-env P1_READONLY_TOKEN --assign luke --dry-run
  python -m prospector pull $seg --cities "Lakewood,Boulder,Broomfield,Golden" --out /tmp/derik-$seg.json
  python -m prospector push /tmp/derik-$seg.json --base-url $PORTAL \
      --token-env P1_READONLY_TOKEN --assign derik --dry-run
done
```

Push `den:customers` before `den:open_jobs`. `--owners` is for the Den
segments only. In a dry run, "inserted" means new people not in the CRM yet.

Then read each rep's `supply` (§ 3) and `GET /nimbus/api/settings`:
`week_research_spend_usd`, `weekly_research_cap_usd`, `month_spend_usd`. What
is left of the week, at about two cents a lead, is how many lookups it buys.

Report, in one screen: days of supply per rep, what each import would add, how
many leads the research money covers and who they should go to (the rep with
fewer days of cards first). End with **"Reply GO to run it."** Then stop.

**Step 2: on "go", and not before.**

1. The same pushes without `--dry-run`. Den first.
2. Research, aimed at whoever is short:
   ```python
   r = s.post(f"{PORTAL}/nimbus/api/b2b/reenrich",
              json={"lead_type": "realtor", "limit": 200, "rep": "derik"})
   run_id = r.json()["run_id"]                       # 429: the week's money is spent
   s.get(f"{PORTAL}/nimbus/api/runs/{run_id}").json()  # poll until status != "running"
   ```
   200 leads a call at most. Roughly one in four comes back with a named
   person, more with a general phone or email. Stop at the first 429.
3. Report what landed: leads imported per rep, contacts found, what it cost,
   and each rep's days of supply now.
4. Delete the `/tmp` files. They hold customer contact details.

**Never add anyone to make up the number from Clay, Apollo, Base44 or a web
search.** If they are not in the CRM, they have not been through suppression.

---

## Memory this mode keeps

None. The `outreach` block of brain memory (`pending_sent`, `sent_threads`,
`reply_stats`, `streak`, `daily_plan`) is retired: the CRM holds all of it.
Do not read it and do not write it.

## The scheduled runs

| When (Denver, weekdays) | What |
|---|---|
| 6:15am | **Build the day:** §§ 2, 2b, 3, 4, then one push with § 6's lines |
| 12:30pm | **Reply sweep:** §§ 2, 2b only. Speak only if a reply needs Luke |
| 4:49pm | **Close-out:** §§ 2, 2b, 6 |
| Friday 3pm | **Scorecard:** the Friday part of § 6 |
| Monday 5am | **Refill proposal:** § 7 step 1, in a session with the repo. Step 2 only when Luke replies GO |
