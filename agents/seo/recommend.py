"""Turn crawl findings and public research into ranked, reviewable recommendations.

Every recommendation carries the full set a human needs to judge it without
re-doing the work: category, city/service context, the customer question or
search intent behind it, the action, the rationale, evidence URLs, a
confidence, and what a person must verify before acting.

Two sources, kept distinct because their epistemic status differs:

  * **Crawl-derived** — direct observations of our own pages. "This page has
    no meta description" is a fact we checked.
  * **Research-derived** — public web research. Indirect by nature, labelled a
    public-research opportunity, and dropped entirely if it arrives without a
    citation.

Ranking is deliberately simple and explainable, in the same spirit as
``agents/content/score.py``: category weight, confidence, evidence depth, and
a boost for the two markets the marketing profile names as priorities.
"""
from .. import config
from . import inspect as page_inspect
from .honesty import PUBLIC_RESEARCH

# How much a category is worth when ranking. A technical fault that blocks
# indexing outranks a nice-to-have content brief, because one is a hole in the
# bucket and the other is more water.
_CATEGORY_WEIGHT = {
    'technical_website_fix':               5.0,
    'improve_existing_page':               4.0,
    'create_city_or_service_area_page':    3.5,
    'create_service_page':                 3.5,
    'google_business_profile_opportunity': 3.0,
    'faq_or_content_brief':                2.5,
    'internal_linking_opportunity':        2.0,
}
_CONFIDENCE_WEIGHT = {'high': 1.5, 'medium': 1.0, 'low': 0.6}

# Title/description lengths where Google reliably truncates in the SERP. These
# are display facts about the search result, not ranking claims.
TITLE_MIN, TITLE_MAX = 25, 60
DESC_MIN, DESC_MAX   = 70, 160
THIN_CONTENT_WORDS   = 300


def _profile():
    return config.load_marketing_profile()


def priority_markets():
    """(label, [example cities]) for the markets the profile prioritises."""
    return [(m['label'], m.get('examples') or [])
            for m in _profile()['service_area'].get('priority_markets') or []]


def approved_services():
    return [(c['key'], c['label']) for c in _profile()['approved_services']['categories']]


def score(rec):
    """0..10, explainable. Category × confidence, plus evidence and market boosts."""
    base = _CATEGORY_WEIGHT.get(rec.get('category'), 1.0)
    conf = _CONFIDENCE_WEIGHT.get(rec.get('confidence'), 0.6)
    evidence_boost = min(len(rec.get('evidence') or []), 4) * 0.25
    market_boost = 0.0
    city = (rec.get('city') or '').lower()
    for _label, examples in priority_markets():
        if city and any(city == e.lower() for e in examples):
            market_boost = 1.0
            break
    return round(min(10.0, base * conf + evidence_boost + market_boost), 2)


def _rec(category, action, rationale, evidence, confidence='medium',
         city='', service='', intent='', review_notes='', kind=''):
    return {
        'category': category, 'city': city, 'service': service,
        'intent': intent, 'action': action, 'rationale': rationale,
        'evidence': list(evidence or []), 'confidence': confidence,
        'evidence_basis': PUBLIC_RESEARCH, 'review_notes': review_notes,
        # Internal: what *sort* of finding this is, so the same fault repeated
        # across pages can be collapsed into one job. Stripped before saving.
        'kind': kind or category,
    }


# A finding that recurs across pages is almost always one template-level fix,
# and listing it once per page buries everything else. Below this threshold
# the per-page detail is more useful than the summary.
GROUP_THRESHOLD = 3
_GROUPED_ACTION = {
    'missing_alt':      'Add alt text to images across {n} pages',
    'no_meta_desc':     'Add meta descriptions to {n} pages',
    'long_meta_desc':   'Shorten the meta description on {n} pages',
    'bad_title_length': 'Rewrite page titles on {n} pages',
    'no_title':         'Add page titles to {n} pages',
    'no_h1':            'Add an H1 heading to {n} pages',
    'multi_h1':         'Reduce {n} pages to a single H1',
    'thin_content':     'Review {n} thin pages — expand or merge',
    'no_viewport':      'Add a viewport meta tag to {n} pages',
    'broken_jsonld':    'Fix malformed JSON-LD on {n} pages',
    'generic_anchors':  'Replace generic link labels on {n} pages',
    'orphan_page':      '{n} pages had no internal link found in the crawl',
    'noindex':          'Confirm the noindex on {n} pages is intentional',
    'dead_page':        'Investigate {n} pages that did not return readable HTML',
}


def group_repeated(recs, threshold=GROUP_THRESHOLD):
    """Collapse the same finding across many pages into one recommendation.

    Without this the queue is dozens of copies of "add alt text to one image
    on <page>" — technically correct and completely unreviewable. One entry
    naming the pattern, carrying sample URLs and a count, is the same
    information in a form somebody can act on.
    """
    buckets, order = {}, []
    for r in recs:
        key = (r['category'], r.get('kind'))
        if key not in buckets:
            buckets[key] = []
            order.append(key)
        buckets[key].append(r)

    out = []
    for key in order:
        group = buckets[key]
        category, kind = key
        if len(group) < threshold or kind not in _GROUPED_ACTION:
            out.extend(group)
            continue
        urls = [u for r in group for u in (r.get('evidence') or [])]
        first = group[0]
        out.append({
            'category': category,
            'city': '', 'service': '', 'intent': '',
            'action': _GROUPED_ACTION[kind].format(n=len(group)) + '.',
            'rationale': (f'The same issue appears on {len(group)} of the pages '
                          f'crawled, which usually means one template is '
                          f'responsible rather than {len(group)} separate '
                          f'authoring mistakes. ' + first.get('rationale', '')),
            'evidence': urls[:8],
            'confidence': first.get('confidence', 'medium'),
            'evidence_basis': PUBLIC_RESEARCH,
            'review_notes': ('Check whether one template change fixes all of '
                             'them before editing pages one at a time. '
                             + first.get('review_notes', '')),
            'kind': kind,
            'affected_pages': len(group),
        })
    return out


# ── Crawl-derived: direct observations of our own pages ─────────────────────

def from_crawl(crawl_result):
    """Recommendations we can justify purely from what is on our own site."""
    recs = []
    pages = [p for p in crawl_result.get('pages') or [] if not p.get('error')]
    broken = [p for p in crawl_result.get('pages') or [] if p.get('error')]

    for page in broken:
        recs.append(_rec(
            'technical_website_fix',
            f'Investigate {page["url"]} — it did not return a readable HTML page.',
            f'The crawler got: {page.get("error")}. A page that cannot be fetched '
            f'cannot be indexed, and if it is linked from the site it is also a '
            f'dead end for visitors.',
            [page['url']], confidence='high',
            review_notes='Confirm in a browser before acting — a transient error '
                         'or a bot-blocking rule can look identical to a broken page.',
            kind='dead_page'))

    # ── Client-side rendering gate ──────────────────────────────────────────
    # If the served HTML is an empty shell, a static crawler cannot see the
    # content — so it must not grade it. Reporting "no H1" on a page whose H1
    # is rendered by JavaScript is exactly the kind of confident-and-wrong
    # output this whole module is built to avoid.
    csr = [p for p in pages if p.get('client_rendered')]
    if csr and len(csr) >= max(1, len(pages) // 2):
        recs.append(_rec(
            'technical_website_fix',
            f'Review how the site is rendered — {len(csr)} of {len(pages)} pages '
            f'return an empty HTML shell.',
            f'The HTML served to a crawler contains a mount point '
            f'(`{csr[0].get("root_container")}`) and almost no text; the page is '
            f'assembled in the browser by JavaScript. Google can usually render '
            f'JavaScript, but it happens on a second pass and is less reliable '
            f'than HTML that arrives complete. Other crawlers, link previews and '
            f'some social scrapers do not render JavaScript at all. '
            f'This is an observation about what the server returns — it is not a '
            f'claim about how the site currently performs in search.',
            [p['url'] for p in csr[:6]], confidence='high',
            review_notes='Confirm with Google Search Console\'s URL Inspection '
                         '("view crawled page") if access is ever granted, or by '
                         'disabling JavaScript in a browser. If the site already '
                         'server-renders or pre-renders for bots, this finding '
                         'does not apply. Talk to whoever maintains the site '
                         'before treating it as a defect.',
            kind='client_rendered'))

    for page in pages:
        url = page['url']

        # Everything below reads page content. Skip it entirely for shells —
        # we would be grading markup that is not the page anyone sees.
        if page.get('client_rendered'):
            continue

        if not page.get('title'):
            recs.append(_rec(
                'improve_existing_page',
                f'Add a page title to {url}.',
                'The page has no <title>. Search engines and browser tabs both '
                'fall back to something arbitrary without one.',
                [url], confidence='high',
                review_notes='Write the title around what the page is actually for; '
                             'do not keyword-stuff it.',
                kind='no_title'))
        elif not (TITLE_MIN <= page.get('title_length', 0) <= TITLE_MAX):
            recs.append(_rec(
                'improve_existing_page',
                f'Rewrite the title on {url} (currently '
                f'{page["title_length"]} characters).',
                f'Google truncates titles in the results display beyond roughly '
                f'{TITLE_MAX} characters, and very short titles waste the space. '
                f'This is about how the result renders, not about ranking.',
                [url], confidence='medium',
                review_notes=f'Aim for {TITLE_MIN}-{TITLE_MAX} characters and keep '
                             f'the city in it where the page is local.',
                kind='bad_title_length'))

        if not page.get('meta_description'):
            recs.append(_rec(
                'improve_existing_page',
                f'Add a meta description to {url}.',
                'No meta description, so Google composes a snippet from page text. '
                'Writing one gives control of what a searcher reads before clicking.',
                [url], confidence='high',
                review_notes='One or two sentences. Must describe this page only.',
                kind='no_meta_desc'))
        elif page.get('meta_description_length', 0) > DESC_MAX:
            recs.append(_rec(
                'improve_existing_page',
                f'Shorten the meta description on {url} '
                f'({page["meta_description_length"]} characters).',
                f'Descriptions past about {DESC_MAX} characters get cut off mid-'
                f'sentence in the results display.',
                [url], confidence='medium',
                review_notes='Front-load the important half in case it still truncates.',
                kind='long_meta_desc'))

        if page.get('h1_count', 0) == 0:
            recs.append(_rec(
                'improve_existing_page',
                f'Add an H1 heading to {url}.',
                'The page has no H1, so there is no single stated subject for the '
                'page in its own markup.',
                [url], confidence='high',
                review_notes='One H1 per page, describing what the page is about.',
                kind='no_h1'))
        elif page.get('h1_count', 0) > 1:
            recs.append(_rec(
                'improve_existing_page',
                f'Reduce {url} to a single H1 (currently {page["h1_count"]}).',
                'Multiple H1s leave the page\'s stated subject ambiguous.',
                [url], confidence='low',
                review_notes='Often a theme/template artefact rather than the '
                             'content — check whether the CMS emits the extras.',
                kind='multi_h1'))

        if page.get('word_count', 0) < THIN_CONTENT_WORDS and page.get('word_count', 0) > 0:
            recs.append(_rec(
                'improve_existing_page',
                f'Expand the content on {url} ({page["word_count"]} words).',
                f'Under ~{THIN_CONTENT_WORDS} words there is usually not enough on '
                f'the page to answer what a visitor came to ask.',
                [url], confidence='low',
                review_notes='Only worth doing if the page has a real job. A thin '
                             'page that duplicates another may be better merged.',
                kind='thin_content'))

        if page.get('images_missing_alt', 0) > 0:
            recs.append(_rec(
                'technical_website_fix',
                f'Add alt text to {page["images_missing_alt"]} image(s) on {url}.',
                'Images without alt text are invisible to screen readers and give '
                'search engines nothing to work with. Accessibility first, search second.',
                [url], confidence='high',
                review_notes='Describe the image. Decorative images take empty alt="".',
                kind='missing_alt'))

        if not page.get('has_viewport'):
            recs.append(_rec(
                'technical_website_fix',
                f'Add a viewport meta tag to {url}.',
                'Without it the page will not scale correctly on phones — which is '
                'how most homeowners will open it.',
                [url], confidence='high',
                review_notes='Check on a real phone, not just a narrow browser window.',
                kind='no_viewport'))

        if page.get('broken_jsonld_blocks', 0):
            recs.append(_rec(
                'technical_website_fix',
                f'Fix {page["broken_jsonld_blocks"]} malformed JSON-LD block(s) on {url}.',
                'Structured data that does not parse is ignored entirely, so the '
                'markup is doing nothing at all right now.',
                [url], confidence='high',
                review_notes='Validate with Google\'s Rich Results Test after fixing.',
                kind='broken_jsonld'))

        if 'noindex' in (page.get('meta_robots') or ''):
            recs.append(_rec(
                'technical_website_fix',
                f'Confirm the noindex on {url} is intentional.',
                'This page tells search engines not to index it. That is correct for '
                'thank-you and admin pages and a serious problem on anything else.',
                [url], confidence='high',
                review_notes='If this is a page we want found, removing noindex is '
                             'the single highest-value fix on this list.',
                kind='noindex'))

    home = next((p for p in pages if p.get('depth') == 0
                 or p['url'].rstrip('/') == crawl_result.get('base_url', '').rstrip('/')),
                pages[0] if pages else None)
    if home:
        missing = page_inspect.missing_schema(home, 'home')
        if missing:
            recs.append(_rec(
                'technical_website_fix',
                f'Add LocalBusiness structured data to {home["url"]}.',
                f'No {" / ".join(missing)} markup found. This is the markup Google '
                f'reads for name, address, phone and service area on a local business.',
                [home['url']], confidence='medium',
                review_notes='Name, address and phone in the markup must match the '
                             'Business Profile exactly. Verify before publishing.'))

    recs.extend(_internal_linking(pages))
    # Collapse site-wide patterns last, so grouping sees every finding.
    return group_repeated(recs)


def _internal_linking(pages):
    """Orphan pages and generic anchors — both visible from the crawl alone."""
    recs = []
    # Links in a client-rendered page are injected by JavaScript, so their
    # absence from the served HTML says nothing about the real navigation.
    pages = [p for p in pages if not p.get('client_rendered')]
    linked = set()
    for p in pages:
        for href in (p.get('internal_links') or []):
            linked.add(href.rstrip('/').split('?')[0])

    for p in pages:
        path = '/' + p['url'].split('/', 3)[-1] if p['url'].count('/') > 2 else '/'
        if p.get('depth', 0) > 0 and path.rstrip('/') not in linked \
                and p['url'].rstrip('/') not in linked:
            recs.append(_rec(
                'internal_linking_opportunity',
                f'Link to {p["url"]} from a relevant page.',
                'This page was reached from the sitemap but no internal link to it '
                'was found in the crawl. Pages nothing links to are harder for both '
                'visitors and crawlers to reach.',
                [p['url']], confidence='low',
                review_notes='Crawl limits mean the link may exist on a page we did '
                             'not reach. Verify before treating it as orphaned.',
                kind='orphan_page'))

    generic = {'click here', 'read more', 'learn more', 'here', 'more'}
    for p in pages:
        hits = [a for a in (p.get('anchor_texts') or []) if a.lower().strip() in generic]
        if len(hits) >= 3:
            recs.append(_rec(
                'internal_linking_opportunity',
                f'Replace {len(hits)} generic link labels on {p["url"]} with '
                f'descriptive text.',
                'Links reading "click here" or "read more" describe nothing — not to '
                'a screen reader, not to a crawler, not to someone scanning the page.',
                [p['url']], confidence='medium',
                review_notes='Use the destination\'s subject as the link text.',
                kind='generic_anchors'))
    return recs


# ── Research-derived: indirect, cited, labelled ─────────────────────────────

def from_research(questions_by_ctx, competitor_by_ctx, existing_pages):
    """Recommendations from public research. Each one must carry citations."""
    recs = []
    have_text = ' '.join(
        (p.get('title', '') + ' ' + ' '.join(p.get('h1') or []) + ' ' +
         ' '.join(p.get('h2') or [])).lower()
        for p in existing_pages)

    from .honesty import FIRST_PARTY
    for (city, service_key, service_label), payload in (questions_by_ctx or {}).items():
        cites = payload.get('citations') or []
        for q in (payload.get('questions') or [])[:6]:
            question = str(q.get('question', '')).strip()
            if not question:
                continue
            # Skip anything the site visibly answers already.
            key_terms = [w for w in question.lower().split() if len(w) > 5][:3]
            if key_terms and all(t in have_text for t in key_terms):
                continue

            # A question our own records show customers asking is a stronger
            # basis than one a model inferred, and it says so on the card.
            src = q.get('source', '')
            first_party = src in ('crm', 'field_note')
            if first_party:
                where = ('our own CRM notes' if src == 'crm'
                         else 'a note somebody typed in after hearing it')
                rationale = (f'This came from {where}, not from public research. '
                             + (str(q.get('why_it_matters', '')).strip() or ''))
                review = ('Confirm the phrasing matches how customers say it. '
                          'Answer from our own experience.')
                own_cites = [c for c in cites
                             if str(c).startswith(('crm:', 'field-note:'))] or cites
            else:
                rationale = (f'Public research indicates this is a question '
                             f'{city} homeowners ask about {service_label}. '
                             + (str(q.get('why_it_matters', '')).strip() or ''))
                review = ('Confirm the question matches what our reps actually '
                          'hear before writing. Answer from our own experience — '
                          'do not restate the research as if it were our data.')
                own_cites = cites

            rec = _rec(
                'faq_or_content_brief',
                f'Write a page or FAQ entry answering: "{question}"',
                rationale, own_cites,
                confidence='high' if first_party else ('medium' if cites else 'low'),
                city=city, service=service_key, intent=question,
                review_notes=review)
            if first_party:
                rec['evidence_basis'] = FIRST_PARTY
            recs.append(rec)

    for (city, service_key, service_label), payload in (competitor_by_ctx or {}).items():
        cites = payload.get('citations') or []
        for gap in (payload.get('content_gaps') or [])[:3]:
            recs.append(_rec(
                'create_service_page' if service_key else 'create_city_or_service_area_page',
                f'Consider a page covering: {gap}',
                f'Public research suggests this topic is covered by other '
                f'{city} contractors and is not obviously covered on our site. '
                f'This is a visibility gap observed on public pages, not a '
                f'measured performance comparison.',
                cites, confidence='low',
                city=city, service=service_key, intent=gap,
                review_notes='Verify we actually do this work and want the lead type '
                             'before writing. Check it is not already covered on a '
                             'page the crawl did not reach.'))
    return recs


def city_service_coverage(pages, cities, services):
    """City × service pages we don't appear to have. Pure gap arithmetic.

    Based on our own sitemap and page titles, so this is an observation about
    our site — but it deliberately claims nothing about whether such a page
    would rank, only that it does not currently exist.
    """
    recs = []
    corpus = ' '.join((p.get('url', '') + ' ' + p.get('title', '')).lower()
                      for p in pages)
    for city in cities:
        for key, label in services:
            if city.lower() in corpus and key in corpus:
                continue
            if city.lower() not in corpus:
                recs.append(_rec(
                    'create_city_or_service_area_page',
                    f'Consider a {city} service-area page.',
                    f'No page on our site mentions {city} in its URL or title. '
                    f'{city} is inside the service area described in the approved '
                    f'marketing profile.',
                    [f'{p["url"]}' for p in pages[:2]] or ['profile:service_area'],
                    confidence='medium', city=city,
                    review_notes='Only build it if we genuinely serve and can staff '
                                 f'{city}. A thin page per town is worse than none — '
                                 'each needs real local content.'))
                break
    return recs


def topic_opportunities(questions_by_ctx, competitor_by_ctx, pages):
    """Candidate topics ranked by intent and business value.

    Sources: researched customer questions, competitor content gaps, and our
    own uncovered city × service combinations. No volume data anywhere — see
    ``intent.py`` for why that is stated rather than quietly omitted.
    """
    from . import intent as intent_mod
    out, seen = [], set()

    def add(text, city='', service=''):
        key = (text or '').strip().lower()
        if not key or key in seen:
            return
        seen.add(key)
        out.append(intent_mod.opportunity(text.strip(), city, service))

    for (city, service_key, _label), payload in (questions_by_ctx or {}).items():
        for q in (payload.get('questions') or []):
            add(str(q.get('question', '')), city, service_key)

    for (city, service_key, _label), payload in (competitor_by_ctx or {}).items():
        for gap in (payload.get('content_gaps') or []):
            add(str(gap), city, service_key)

    # Our own coverage gaps are topic opportunities too, and they are the ones
    # we can state most confidently — we checked our own sitemap.
    corpus = ' '.join((p.get('url', '') + ' ' + p.get('title', '')).lower()
                      for p in pages)
    for _label, examples in priority_markets():
        for city in examples:
            for key, service_label in approved_services():
                if city.lower() not in corpus:
                    add(f'{service_label} in {city}', city, key)
                    break

    return intent_mod.rank(out)


# One week. More than this is a backlog pretending to be a plan.
_PLAN_DAYS = ('Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday')


def content_plan(opportunities, recs):
    """A week of work, drawn from the highest-value topics we can act on.

    Page type follows intent: somebody ready to hire wants a service or city
    page, somebody learning wants an FAQ or article. Deliberately capped at
    five — the queue is the backlog, this is the week.
    """
    from . import intent as intent_mod

    page_type_for = {
        intent_mod.TRANSACTIONAL: 'service or city page',
        intent_mod.COMMERCIAL:    'comparison page',
        intent_mod.INFORMATIONAL: 'FAQ or article',
        intent_mod.NAVIGATIONAL:  'Google Business post',
    }
    plan = []
    for day, o in zip(_PLAN_DAYS, opportunities):
        plan.append({
            'day': day,
            'title': o['topic'],
            'page_type': page_type_for.get(o['intent'], 'article'),
            'intent': o['intent'],
            'city': o.get('city', ''),
            'service': o.get('service', ''),
            'business_value': o['business_value'],
        })
    return plan


def build_all(crawl_result, questions_by_ctx=None, competitor_by_ctx=None):
    """Everything, scored and sorted. Honesty filtering happens in run.py."""
    pages = [p for p in crawl_result.get('pages') or [] if not p.get('error')]
    recs = from_crawl(crawl_result)
    recs += from_research(questions_by_ctx, competitor_by_ctx, pages)

    cities = []
    for _label, examples in priority_markets():
        cities.extend(examples)
    recs += city_service_coverage(pages, cities, approved_services())

    for r in recs:
        r['score'] = score(r)
        # A site-wide pattern is worth more than a single page's copy of it.
        if r.get('affected_pages', 1) > 1:
            r['score'] = round(min(10.0, r['score'] + 0.5), 2)
    recs.sort(key=lambda r: -r['score'])
    return recs
