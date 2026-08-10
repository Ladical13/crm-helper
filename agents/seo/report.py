"""Render the weekly SEO strategy report as Markdown.

Section order follows the agreed weekly-report spec. Two of its sections —
**Search Console winners and decliners** and **pages losing visibility** —
cannot be produced without Search Console, which is owner-gated.

They are still rendered, as explicitly blocked sections saying what is missing
and what it would take. Omitting them would be worse: a reader comparing this
against the spec would assume they were forgotten, and a reader who never saw
the spec would never learn that the most valuable half of an SEO report is
absent by constraint rather than by choice.

The header states what the strategist could and could not see, for the same
reason. Nothing here is a measured result.
"""
from datetime import datetime

from .honesty import OWNED_DATA
from .intent import INTENT_LABEL

_CATEGORY_LABEL = {
    'improve_existing_page':               'Improve an existing page',
    'create_service_page':                 'New service page',
    'create_city_or_service_area_page':    'New city / service-area page',
    'faq_or_content_brief':                'FAQ or content brief',
    'internal_linking_opportunity':        'Internal linking',
    'technical_website_fix':               'Technical fix',
    'google_business_profile_opportunity': 'Google Business Profile',
}

_PAGE_WORK = ('improve_existing_page', 'create_service_page',
              'create_city_or_service_area_page', 'faq_or_content_brief')


def week_of(when=None):
    d = when or datetime.utcnow()
    monday = d.fromordinal(d.toordinal() - d.weekday())
    return monday.strftime('%Y-%m-%d')


def _blocked_section(a, title, needs, would_show):
    a(f'## {title}')
    a('')
    a(f'> **Not available — {needs}.**')
    a('>')
    a(f'> This section would show {would_show} It cannot be produced from '
      f'public research: no amount of crawling or web search reveals what '
      f'people searched, what we ranked for, or whether that changed. '
      f'Anything printed here without that data would be invention.')
    a('')
    a('See `agents/MARKETING_PLAN.md` for what it would take to unblock.')
    a('')


def render(run, crawl_result, recs, dropped, research_notes, opportunities=None,
           content_plan=None):
    """Return the Markdown body of the weekly report."""
    lines = []
    a = lines.append
    opportunities = opportunities or []
    content_plan = content_plan or []

    a(f'# Local SEO strategy — week of {week_of()}')
    a('')
    a(f'_Generated {run.get("started_at", "")} · mode: **{run.get("mode", "live")}**_')
    a('')

    # ── Sources ──
    pages = crawl_result.get('pages') or []
    csr = [p for p in pages if p.get('client_rendered')]
    a('## What this report is based on')
    a('')
    a('| Source | Status |')
    a('|---|---|')
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

    # ── 1. Topic opportunities by intent and business value ──
    a('## 1. Local topic opportunities, by intent and business value')
    a('')
    if opportunities:
        a('Candidate topics from public research and our own service-area gaps, '
          'sorted by how close the intent sits to work we sell. **These are '
          'topics, not keywords** — there is no search volume or difficulty '
          'behind them, because we have no keyword tool and no Search Console. '
          'The ordering is a judgement, shown with its reasoning so you can '
          'disagree with it.')
        a('')
        a('| # | Topic | Intent | Value | City | Why that intent |')
        a('|---|---|---|---|---|---|')
        for i, o in enumerate(opportunities[:15], 1):
            a(f'| {i} | {o["topic"][:70]} | {INTENT_LABEL.get(o["intent"], o["intent"])} '
              f'| {o["business_value"]} | {o.get("city") or "—"} | {o["intent_reason"]} |')
        a('')
    else:
        a('_No topic opportunities this week — public research returned nothing '
          'usable, or the spend cap was reached. Check the sources table above._')
        a('')

    # ── 2 & 3. The blocked sections ──
    _blocked_section(
        a, '2. Search Console winners and decliners',
        'requires Google Search Console',
        'which queries and pages gained or lost clicks, impressions and average '
        'position against the previous period.')
    _blocked_section(
        a, '3. Pages losing visibility',
        'requires Google Search Console',
        'pages whose impressions or positions declined, which is the earliest '
        'warning that something has gone wrong on a page that used to work.')

    # ── 4. Technical ──
    tech = [r for r in recs if r['category'] == 'technical_website_fix']
    a('## 4. Technical website issues')
    a('')
    if tech:
        for r in tech:
            n = r.get('affected_pages', 1)
            a(f'- **{r["action"]}**' + (f' _(affects {n} pages)_' if n > 1 else ''))
            a(f'  - {r.get("rationale", "").strip()}')
            a(f'  - _Before acting:_ {r.get("review_notes", "").strip()}')
        a('')
    else:
        a('_Nothing found in this crawl._')
        a('')

    # ── 5. Competitor / content gaps ──
    gaps = [r for r in recs
            if r['category'] in ('create_service_page',
                                 'create_city_or_service_area_page')]
    a('## 5. Competitor and content gaps')
    a('')
    if gaps:
        a('Topics visible on other Colorado contractors\' public pages, or '
          'service-area coverage we do not appear to have. Gaps in **coverage**, '
          'observed on public pages — not a performance comparison.')
        a('')
        for r in gaps:
            a(f'- **{r["action"]}** — {r.get("rationale", "").strip()}')
        a('')
    else:
        a('_No coverage gaps identified this week._')
        a('')

    # ── 6. The five ──
    page_work = [r for r in recs if r['category'] in _PAGE_WORK][:5]
    a('## 6. Five recommended page improvements or new pages')
    a('')
    if page_work:
        for i, r in enumerate(page_work, 1):
            ctx = ' · '.join(x for x in (r.get('city'), r.get('service')) if x)
            a(f'**{i}. {r["action"]}**')
            a('')
            a(f'- Category: {_CATEGORY_LABEL.get(r["category"], r["category"])}'
              + (f' · {ctx}' if ctx else ''))
            if r.get('intent'):
                a(f'- Customer question: “{r["intent"]}”')
            a(f'- Basis: {r.get("label") or "—"} · confidence {r.get("confidence")}')
            a(f'- Why: {r.get("rationale", "").strip()}')
            a(f'- Before acting: {r.get("review_notes", "").strip()}')
            ev = r.get('evidence') or []
            if ev:
                a(f'- Evidence: {", ".join(ev[:4])}')
            a('')
    else:
        a('_No page-level work surfaced above the evidence bar this week._')
        a('')

    # ── 7. Weekly content plan ──
    a('## 7. Weekly content plan')
    a('')
    if content_plan:
        a('One week of work, ordered. Tied to **researched customer questions**, '
          'not to measured search demand — that distinction matters and is not '
          'pedantry: we cannot see demand, so this is a plan built on what '
          'public sources say people ask, which is a weaker basis than data '
          'and a stronger one than guessing.')
        a('')
        a('| Day | Piece | Page type | Intent | City |')
        a('|---|---|---|---|---|')
        for item in content_plan:
            a(f'| {item["day"]} | {item["title"][:60]} | {item["page_type"]} '
              f'| {INTENT_LABEL.get(item["intent"], item["intent"])} '
              f'| {item.get("city") or "—"} |')
        a('')
        a('_Each row needs a content brief before anything is written. '
          'Approve the matching recommendation in Nimbus to generate one._')
        a('')
    else:
        a('_No plan generated — there were no approved-service topics with '
          'sources this week._')
        a('')

    # ── The full queue ──
    a('## Full recommendation queue')
    a('')
    if not recs:
        a('The crawl completed and produced nothing above the evidence bar. '
          'That is a real result, not an error.')
        a('')
    else:
        by_cat = {}
        for r in recs:
            by_cat.setdefault(r['category'], []).append(r)
        a('| Category | Count |')
        a('|---|---|')
        for cat, items in sorted(by_cat.items(), key=lambda kv: -len(kv[1])):
            a(f'| {_CATEGORY_LABEL.get(cat, cat)} | {len(items)} |')
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
        a('Generated and then rejected by the honesty checks. Listed so a run '
          'that produces little does not look like a run that found little.')
        a('')
        for rec, reason in dropped[:25]:
            a(f'- `{rec.get("category", "?")}` — {reason}')
        a('')

    return '\n'.join(lines)
