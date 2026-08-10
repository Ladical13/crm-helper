"""Render the weekly SEO strategy report as Markdown.

The report leads with what the strategist could and could not see. That header
is not boilerplate: a reader who does not know GA4 and Search Console are
absent will read "no traffic data" as an oversight rather than a constraint,
and may act as though the recommendations are performance-driven. They aren't.
"""
from datetime import datetime

from .honesty import OWNED_DATA

_CATEGORY_LABEL = {
    'improve_existing_page':               'Improve an existing page',
    'create_service_page':                 'New service page',
    'create_city_or_service_area_page':    'New city / service-area page',
    'faq_or_content_brief':                'FAQ or content brief',
    'internal_linking_opportunity':        'Internal linking',
    'technical_website_fix':               'Technical fix',
    'google_business_profile_opportunity': 'Google Business Profile',
}


def week_of(when=None):
    d = when or datetime.utcnow()
    monday = d.fromordinal(d.toordinal() - d.weekday())
    return monday.strftime('%Y-%m-%d')


def render(run, crawl_result, recs, dropped, research_notes):
    """Return the Markdown body of the weekly report."""
    lines = []
    a = lines.append

    a(f'# Local SEO strategy — week of {week_of()}')
    a('')
    a(f'_Generated {run.get("started_at", "")} · '
      f'mode: **{run.get("mode", "live")}**_')
    a('')

    # ── What this is and is not ──
    a('## What this report is based on')
    a('')
    a('| Source | Status |')
    a('|---|---|')
    pages = crawl_result.get('pages') or []
    csr = [p for p in pages if p.get('client_rendered')]
    a(f'| Our public website | crawled — {len(pages)} pages |')
    if csr:
        a(f'| Page content | **not readable statically** — {len(csr)} pages are '
          f'assembled by JavaScript |')
    a(f'| Sitemap | {crawl_result.get("sitemap_note", "—")} |')
    a(f'| robots.txt | {crawl_result.get("robots_note", "—")} |')
    a(f'| Public web research | {research_notes or "—"} |')
    a('| Google Search Console | **not available** — owner access required |')
    a('| Google Analytics 4 | **not available** — owner access required |')
    a('')
    a('> **Read this before acting on anything below.** Nothing here is a '
      'measured result. Without Search Console or Analytics we cannot see our '
      'rankings, our search volume, our traffic, or how any competitor '
      'performs — and so this report never states one. Findings are either '
      'direct observations of our own pages, or public-research opportunities '
      'with the sources attached. Every recommendation needs a human to '
      'confirm it before anything changes.')
    a('')

    if not recs:
        a('## No recommendations this week')
        a('')
        a('The crawl completed and produced nothing above the evidence bar. '
          'That is a real result, not an error — check the run detail if it '
          'looks wrong.')
        return '\n'.join(lines)

    # ── Summary ──
    by_cat = {}
    for r in recs:
        by_cat.setdefault(r['category'], []).append(r)

    a(f'## {len(recs)} recommendations, awaiting review')
    a('')
    a('| Category | Count |')
    a('|---|---|')
    for cat, items in sorted(by_cat.items(), key=lambda kv: -len(kv[1])):
        a(f'| {_CATEGORY_LABEL.get(cat, cat)} | {len(items)} |')
    a('')

    # ── The queue ──
    a('## Ranked queue')
    a('')
    for i, r in enumerate(recs, 1):
        ctx = ' · '.join(x for x in (r.get('city'), r.get('service')) if x)
        a(f'### {i}. {r["action"]}')
        a('')
        a(f'- **Category:** {_CATEGORY_LABEL.get(r["category"], r["category"])}')
        if ctx:
            a(f'- **Where / what:** {ctx}')
        if r.get('intent'):
            a(f'- **Customer question / intent:** {r["intent"]}')
        a(f'- **Basis:** {r.get("label") or "—"} '
          f'({"owned data" if r.get("evidence_basis") == OWNED_DATA else "indirect evidence"})')
        a(f'- **Confidence:** {r.get("confidence")}')
        a(f'- **Why:** {r.get("rationale", "").strip()}')
        a(f'- **Before acting:** {r.get("review_notes", "").strip()}')
        ev = r.get('evidence') or []
        if ev:
            a(f'- **Evidence:** {", ".join(ev[:6])}')
        a('')

    if dropped:
        a('## Dropped before review')
        a('')
        a('These were generated and then rejected by the honesty checks. Listed '
          'so a run that produces little does not look like a run that found '
          'little.')
        a('')
        for rec, reason in dropped[:25]:
            a(f'- `{rec.get("category", "?")}` — {reason}')
        a('')

    return '\n'.join(lines)
