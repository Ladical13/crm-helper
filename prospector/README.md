# Prospector — Colorado partner sourcing

Feeds The Pipeline's partner queue from **free Colorado open data**. No API keys,
no per-contact credits, no vendor account.

```bash
python -m prospector segments --count                      # what's out there, live
python -m prospector pull dora:hoa --out prospector/inbox/hoa.json
python -m prospector push prospector/inbox/hoa.json \
    --base-url http://localhost:5010 --user luke --dry-run  # ALWAYS dry-run first
python -m pytest prospector/tests                          # 27 tests, offline
```

## Warm lists: the Den

`den:customers`, `den:open_jobs` and `den:partners` read the people who already
know us out of the Den (Colorado only) instead of open data. They need
`BASE44_TOKEN`, so run them where it lives, and they import as WARM leads with
a cadence - the pull file records how, and `push` forwards it.

```bash
railway run --service project-one-estimator -- \
    python -m prospector pull den:customers --out prospector/inbox/den-customers.json
python -m prospector push prospector/inbox/den-customers.json \
    --base-url https://project-one-estimator-production.up.railway.app \
    --user luke --assign luke --dry-run
```

Push `den:customers` before `den:open_jobs`. `sources/den.py` leaves out anyone
with a job in production, red-flag customers, and any open job touched in the
last two weeks. `--token-env P1_READONLY_TOKEN` signs in with the API token
instead of a password (the importer then insists on `--assign`).

Every Den row carries the job's salesperson as `owner`. `--owners derik` lets
the reps you name keep the customers the Den says are theirs; everyone else's
(another market's reps, people who have left, jobs with nobody on them) go to
`--assign`. Without `--owners`, every row goes to `--assign`.

## Keeping a statewide list local

```bash
python -m prospector pull cdos:realty --cities noco --out prospector/inbox/realty.json
```

`--cities noco` keeps Larimer and Weld counties; a comma list works too. These
rows have no phone or email, so they land in "Needs research". Nimbus fills
them in: `POST /nimbus/api/b2b/reenrich` with a `lead_type` and a `limit`.

## Segments

Counts are live as of 2026-07-28, filtered to Colorado and Active / Good Standing.

| Segment | Lead type | Count | Address? | Person? |
|---|---|---|---|---|
| `dora:hoa` | hoa | 8,536 | no | no |
| `dora:hoa_agent` | property_manager | 7,349 | no | sometimes |
| `dora:brokerage` | realtor | 5,215 | no | no |
| `cdos:property_manager` | property_manager | 1,914 | **yes** | **~68%** |
| `cdos:hoa_manager` | property_manager | 397 | **yes** | **yes** |
| `cdos:insurance_agent` | insurance_agent | 1,282 | **yes** | **yes** |
| `cdos:realty` | realtor | 11,762 | **yes** | **yes** |
| `dora:broker` | realtor | 40,264 | no | — **off by default** |

~36,500 company-level prospects without spending anything.

**Start with `dora:hoa`.** An HOA is a *direct* commercial buyer, not just a
referral source — a 40-unit townhome association is a large roof — and it is the
least-worked list in the state.

**`dora:broker` is off by default on purpose.** DORA publishes no email, phone or
street address, so all 40,264 individual brokers would need paid enrichment.
Work `dora:brokerage` instead: one visit to an office reaches every agent in it.

## The two sources

**DORA** (`4zse-6bnw`) — Division of Real Estate licensees. Name, city, zip,
licence number, status. **No contact details at all.** Its value is coverage:
every registered HOA and brokerage in Colorado.

**CDOS** (`4ykn-tg5h`) — Secretary of State business entities. Carries a
**principal street address** and often a **registered agent's name**, which is
what makes the free tier work — every row is callable and droppable as-is.

Two traps, both handled in code:

- Lapsed CDOS entities store their status *inside the name*
  (`"ARG PROPERTY MANAGEMENT CORPORATION, Delinquent September 1, 2009"`).
  `normalize.clean_entity_name` strips it; the Good Standing filter avoids most.
- The registered agent is sometimes a corporate filing service (CSC, CT
  Corporation). Those are a mailing address, not a prospect, so they are not
  treated as a named person.

Colorado publishes **no free bulk list of insurance producers** — licensing runs
through Sircon/NIPR, lookup-only. `cdos:insurance_agent` matches agency *names*
in the business registry, which is the only free path. Expect it to be thinner
than the others.

## How it fits together

```
open data  ->  prospector  ->  POST /crm/api/prospects/import  ->  leads
                                        ^
                            dedupe + suppression + assignment
                                   all live server-side
```

This package is deliberately dumb. It does not know what is already in the CRM,
does not filter opt-outs, and does not assign reps — all of that is in
`salescrm/app.py` so that **re-running any pull is always safe**. Import is
idempotent: the same rows twice insert nothing.

`inbox/` is the drop zone. Browser-harvested rows (a brokerage's public staff
page, say) go here in the same shape and push through the same path — which is
also how a paid enricher would be switched on later, with no rework.

## Row shape

`normalize.FIELDS` plus `icp_score`. Only `source_ref` and the dedupe keys are
load-bearing:

- **`source_ref`** — stable id of the originating record (`dora:4zse-6bnw:51739`).
  Most open-data rows have no phone or email, so without this there is nothing to
  match on and every re-import would duplicate them. The importer rejects a row
  that has no phone, email, licence *or* `source_ref`.
- **`icp_score`** — 0–6, queue ordering only, never a filter. +2 street address,
  +2 Front Range city, +1 named person, +1 active. A low score is still a real
  partner, just not the one to call first.
