"""Nimbus — AI agent stack for Project One Roofing.

Two agents run today:

  * ``agents.b2b`` — per-rep B2B lead generation. Pulls from free open data
    first (existing prospector sources) and falls back to Perplexity for
    segments the free feeds don't cover (churches, schools, GCs, commercial).
    Enriches with cold-call intel and a storm join against the canvasser's
    NOAA hail cache. Writes go through ``POST /crm/api/prospects/import`` —
    never directly to salescrm.db, and never to Base44 / the Den.

  * ``agents.content`` — social listening and drafts. Pulls Reddit / Google
    Trends / YouTube / GDELT (all free) and uses Perplexity to synthesize
    trending topics into platform-tailored post drafts. Drafts only — a human
    publishes.

Runs in the same Python process as the portal (blueprint at ``/nimbus``).
Sync end-to-end; no async framework. Cost knobs live in
``DATA_DIR/agents/territories.json`` (per-rep) and Nimbus Settings (global).
"""

__version__ = '0.1.0'
