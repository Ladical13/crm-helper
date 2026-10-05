<!-- Source of truth for the Jarvis skill file references/outreach-playbook.md.
     Upload changes via claude.ai → Customize → Skills; keep this copy in step. -->

# The Outreach Playbook

How Jarvis chooses the words. The mechanics are in `daily-outreach.md`.

It is built from the people who have actually taught this, and each of them
is here for one thing. When two of them disagree, the house rules at the
bottom win.

---

## 1. The operating system

| Whose idea | The rule | What Jarvis does with it |
|---|---|---|
| **Alex Hormozi**, *$100M Leads* | Rule of 100: a hundred outreaches every day, and consistency beats talent | The number is 100 and it is reported every day as a streak. No zero days |
| Hormozi | Warm before cold: people who know you convert several times better per touch | Every day's list is ordered customers → existing partners → people who replied → cold |
| Hormozi | Lead with something of value, then ask | Every first touch offers a specific free thing (below). Nobody is "introduced to our services" |
| **Grant Cardone** | Follow up until you get a decision. Silence is not a no, and most people stop after one or two tries | Every logged touch books the next one. A lead leaves the list only on an answer, an opt-out, or four unanswered tries in a row |
| **Jeb Blount**, *Fanatical Prospecting* | Prospect in fixed blocks, on every channel, and replace what you close | Two blocks a day (email, then phone). Email → call → voicemail → text, in that order. A supply check every morning |
| **Chet Holmes**, *Dream 100* | A short list of the best buyers, worked relentlessly, beats a long list worked once | The top realtors, management companies and HOA managers in Larimer and Weld get every touch in the sequence, and a drop-by |
| **Dan Martell**, *Buy Back Your Time* | Keep only the work that needs you. Hand off the first and last ten percent as well as the middle | Luke sends, dials and decides. Lists, drafts, logging, reply triage and the score are Jarvis's, without being asked |
| **Chris Voss**, *Never Split the Difference* | People say no more easily than yes, and a no keeps them talking | The last email in a sequence asks a no-oriented question. Never "just following up" |

---

## 2. What to give, by audience

A first touch names one free, specific, useful thing. Use what is live in
`GET /crm/api/offers`; these are the angles behind them.

| Audience | The give | Why it matters to them in winter |
|---|---|---|
| **Past customers** | A free roof check after any wind, hail or snow | It is their roof and we already did it. Then the ask: who do they know |
| **Open jobs** (inspected or quoted, never decided) | Refresh the number at no charge | Prices and the schedule moved. Many policies also want hail damage reported within about a year of the storm; their policy has the date |
| **Realtors** | A written roof answer inside the inspection deadline, and a roof certification if the lender asks | Listings are slow now. Spring listings are being prepared, and a roof is the surprise that kills a closing |
| **HOAs** | A free roof condition report for the board: every building photographed, remaining life estimated | Boards set next year's budget and hold annual meetings in the winter. They need a number |
| **Property managers** | One call for a leak, someone out within 24 hours weather permitting, photos the owner can read | Winter wind and ice find the weak spots, and the owner calls the manager |
| **Insurance agents** | A roofer to hand clients after a storm who documents properly and never pushes a claim; a roof condition write-up at renewal | Carriers ask about roof age and condition at renewal. A bad contractor makes the agent look bad |
| **Commercial, churches, schools** | A free roof assessment with a remaining-life estimate to budget from | Budgets for next year are being written now |

**Never** offer to pay, waive or rebate a deductible, and never promise a
claim outcome. Colorado law forbids the first and nobody controls the second.

---

## 3. Writing the email

The server gives every card a draft. Jarvis improves the first two lines and
leaves the rest.

1. **The first line is about them, and it is true.** Use `research_notes`,
   `hook`, `recent_storm`, their `company` and `city`. A listing they have, a
   building they manage, a storm that crossed their zip, something from their
   own website. If there is nothing true and specific, do not invent it; send
   the template's own opening.
2. **One idea, one ask.** The ask is a question they can answer in a word:
   "Worth a look?" "Who handles the roofs there?" "Want me on your list?"
3. **Hormozi's A-C-A for anyone who already knows us:** acknowledge something
   real, give them credit for it, then ask. *"Saw you closed the Windsor
   listing in nine days. That is quick for this market. When the next
   inspection flags a roof, do you already have someone who can turn a
   written answer in two days?"*
4. **Subject lines:** three to six words, lower-key, no punctuation tricks.
   A follow-up replies to its own thread ("Re: ...").
5. **The sequence, by touch:**

| Touch | What it is | Shape |
|---|---|---|
| 1 | The give | Their situation, the free thing, one question. No link |
| 2 | The bump | Two sentences on the same thread: a new reason (a storm, a deadline, a season), the same question. An offer link is allowed here |
| 3 | Proof, plainly | What we actually do, in one line a customer would recognise. No superlatives |
| 4 | The no-oriented close | *"Have you given up on getting the roof looked at this year?"* or *"Is it a bad idea for me to check back in the spring?"* Then stop |

The CRM picks first / followup / breakup by how many touches a lead has had.
Follow its step; these shapes say how to write each one.

---

## 4. On the phone

The Outreach tab puts the script on the card. When Luke asks for help with a
call, a voicemail or a brush-off, this is the frame.

**The call opener (Blount), in five beats, under fifteen seconds:**

1. Their name. 2. "It's Luke with Project One Roofing in Loveland."
3. Why you are calling, as a because: "I'm calling because …"
4. The give. 5. The ask, and then stop talking.

*"Dana, it's Luke with Project One Roofing in Loveland. I'm calling because
you list a lot of homes in Windsor, and when an inspection flags a roof I can
get you a written answer in two days. Want me on your list for the next one?"*

**The voicemail:** name, company, number, one reason, number again. Twenty
seconds. Then the text follows the same day.

**The brush-off (Blount: ledge, disrupt, ask):**

| They say | Ledge | Disrupt, then ask again |
|---|---|---|
| "We already have a roofer." | "That makes sense, most good agents do." | "I'm not asking you to switch. When yours is three weeks out and the deadline is Friday, would a second name help?" |
| "Just send me something." | "Happy to." | "So I send the right thing: is it the inspection deadline that bites, or finding someone who'll put it in writing?" |
| "Not interested." | "Fair enough." | "Is it a bad idea if I check back after the next hail?" If the answer is still no, log it and leave them alone |
| "Call me later." | "Will do." | "Is Tuesday morning better, or Thursday?" Then log the callback with the date |

**Cardone's rule for all of them:** agree first. Nobody was ever argued into
a roofer.

**Texts** go to people who know us, or follow a voicemail the same day. A
first text to a stranger always says how to stop ("Reply STOP and I won't
text again"). One thought, no link unless they asked for it.

---

## 5. Replies

- **Interested:** answer the same hour if Luke is around. Draft two specific
  times from his calendar. He books; Jarvis never does.
- **A question:** answer it in one or two sentences, then ask for the visit.
- **"Not now":** thank them, ask when, log it. The CRM brings them back.
- **"Stop":** record it immediately. No reply, no "sorry to see you go".

---

## 6. House rules (these win)

- **Jarvis drafts. Luke sends.** Always.
- **Emails are under 100 words** above the signature. **Texts are under 320
  characters.**
- **Never open with** "just checking in", "just wanted to follow up",
  "touching base", "circling back", "I hope this email finds you well",
  "reaching out to see if", "per my last email".
- **No claims we cannot prove:** no "best", "#1", "premier", "top-rated",
  "guaranteed", "lowest price", no counts of roofs or years.
- **Keep the signature block exactly as the CRM wrote it.** It carries the
  postal address and the way to opt out, which is what makes the email lawful
  to send.
- **No link in a first cold email or text.**
- **Email volume follows the ramp** in `daily-outreach.md` § 4. More is not
  better if it puts estimates and contracts in the spam folder.
- **Calls and texts between 8am and 8pm**, business contacts in business
  hours. Homeowners who are not our customers are not cold-called or
  cold-texted from this list; the CRM already keeps them out.
- **This is a guard rail, not legal advice.** When a question is about what
  the law allows, say so and say Luke should ask counsel.
