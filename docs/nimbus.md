# Nimbus maintenance notes

Nimbus is mounted at `/nimbus` and requires a portal admin account.

## Reliability rules

- SEO and social runs, whether started manually or by the scheduler, share a
  SQLite job lease in `AGENTS_DATA_DIR/nimbus.db`. A partial unique index permits
  one active marketing job. Do not replace it with a process-local lock:
  production uses two gunicorn workers.
- The start response includes `job_id`. Poll `/nimbus/api/seo/result?job_id=...`
  for that job, not whichever job ran most recently. Progress and preview
  results survive page refreshes and worker changes. A worker sends a heartbeat
  every 30 seconds; five minutes without a heartbeat marks the job failed and
  releases the slot. Work interrupted by a restart is not automatically retried.
- SEO/social previews save job progress and their result, but do not create
  recommendations, reports, or content drafts. The underlying agents retain
  their existing dry-run behavior.
- B2B research write-back uses the importer's exact `(row, lead_id)` matches.
  Row offsets must survive chunking. Invalid/suppressed records have no match;
  duplicates may match existing leads. Never infer identity from list position,
  timestamps, or a batch query. Unenriched rows must not erase saved research.
- `/crm/api/pipeline/summary` aggregates the complete visible pipeline in SQL.
  Reps see their own totals; managers/admins see everyone. Won totals are
  explicitly all-time. A failed CRM read must not look like a zero pipeline.
- Supervisor conversations are claimed with a conditional database update
  before launching a thread. A second message receives 409 while one is running.

The changes add `background_jobs` and its unique index when Nimbus opens its
database. Back up the live volume before deployment/migration, and deploy the
whole repository through the single Railway service. Tests use temporary data.

## Verification

`python run_tests.py` runs all seven repository suites. Specific regressions
live in `agents/tests/test_jobs.py`, `portal/tests/test_nimbus_regressions.py`,
`portal/tests/test_nimbus_ui.py`, `agents/tests/test_supervisor.py`, and
`salescrm/tests/test_pipeline_summary.py`. The UI polling checks require Node.

## Potential next integrations

1. **Bing Webmaster Tools:** Nimbus already has the reporting integration in
   `agents/seo/bing.py`. Verify the site and configure its API access if it is
   not connected. It supplies measured Bing performance, not Google rankings.
   [Microsoft API documentation](https://learn.microsoft.com/en-us/bingwebmaster/).
2. **Google Business Profile performance:** add performance reporting using
   the available API after confirming account/API access. Existing Nimbus
   connection checks are not a complete performance dashboard.
   [Google API documentation](https://developers.google.com/my-business/reference/performance/rest).
3. **Apollo contact enrichment:** an optional next step for prospects missing
   decision-maker contact details. Requires API integration and an explicit
   enrichment budget; data availability and credit usage vary by request.
   [Apollo enrichment documentation](https://docs.apollo.io/docs/enrich-people-data).

These are recommendations; no new service was connected or purchased as part
of the reliability fixes. See `agents/CONNECTIONS.md` for the existing setup
instructions and previously recorded access constraints.
