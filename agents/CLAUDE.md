# Nimbus — the AI agents

Marketing, research and outreach agents. Talks to the CRM through its HTTP
API and never reaches into its module or its database.

*Directory-scoped notes: these load when work touches this directory, so the
root `CLAUDE.md` can stay short enough to be read every session. The traps
that cross all four apps — mobile, the clock, the session and mount rules,
deploying — live there, and they apply here too.*

## Networking events (`agents/events.py`)

Which rooms are worth an evening. 🤝 Networking in Nimbus; one Perplexity
search per configured city (`event_cities` in Nimbus Settings), on a weekly
scheduled job.

**A list of events near Fort Collins is a Google search.** What makes this
worth running is the ranking: an event scores on whether the room is full of
the partner segment the CRM is THIN on. The fortieth realtor coffee this year
is not worth an evening and the second insurance agent is, and no search engine
knows that. Proximity is a tiebreak; the partner gap is the score.

- **Nimbus reads the counts through `GET /crm/api/partners/counts`**, never
  `salescrm.db` — the boundary `agents/__init__.py` states. Every partner type
  is reported including the zeros, because a type the caller cannot see is one
  it would have to guess about. `score()` takes the counts as an ARGUMENT, the
  same reason `commercial_fastening` takes its table, so it is testable with no
  database at all.
- **A segment absent from the counts is not scored as zero.** Absent is "no
  evidence", and treating it as empty invents a gap and then says "you have 0"
  about a number nobody supplied.
- **A list of events DECAYS, so past ones are dropped at READ time.**
  `upcoming()` is the only read path for that reason: the table outlives the
  search that filled it, and Nimbus naming a meeting that happened last Tuesday
  costs the page its credibility. `starts_at` is a LOCAL wall-clock date —
  a 7am chamber breakfast in Fort Collins is at 7am in Fort Collins — and
  `company_today()` falls back to UTC, which can only drop an event EARLY.
- **An event with no date, or no URL, is dropped on the way in.** A row that
  cannot go in a calendar cannot be acted on and can never expire; the citation
  is the same non-negotiable defence against invention the B2B sources apply.
- **A decision is STICKY.** A re-run refreshes venue, time and cost and never
  touches `decision` — same rule, and the same reason, as `save_estimate()`
  re-applying a customer's accepted upgrade.
- **The scheduled run scores WITHOUT counts and says so.** It has no session,
  so it cannot read the CRM; `gap_aware` is false on the page until someone
  hits Re-rank, which is an explicit write rather than a GET that quietly
  rewrites rows.
- **Events cache for `CACHE_TTL_DAYS` (3), not the global 30.** A fortnight-old
  answer has stale times, moved venues and events that already happened.
- Guarded by `agents/tests/test_events.py` and
  `salescrm/tests/test_partner_counts.py`.
