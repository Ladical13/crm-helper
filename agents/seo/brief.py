"""Content Brief bot — turns an approved recommendation into a structured brief.

Sits between the strategist and any future draft writer. The order is
deliberate: the strategist decides *what* is worth doing, the brief decides
*what the page must contain and prove*, and only then would anything write
copy. Skipping the brief is how you end up with a flood of blog posts that
answer nothing.

**Briefs are only generated from approved recommendations.** A brief for
something nobody signed off on is just more output.

Two things this refuses to do:

  * **Invent proof.** Required assets are drawn from the marketing profile.
    `provable_differentiators` is currently empty, so every brief says so and
    forbids comparative claims outright rather than leaving a tempting gap.
  * **Promise measurement we cannot deliver.** The "how success is measured"
    section states plainly what is measurable today (page exists, is indexed,
    is internally linked) and what is not (rankings, organic traffic,
    attributed leads) and why.
"""
import json

from .. import config
from . import intent as intent_mod

PAGE_TYPES = ('service_page', 'city_page', 'blog', 'faq',
              'google_business_post', 'email')

# Which page type suits which recommendation. A city gap needs a city page; a
# researched question needs an answer, not a sales page.
_TYPE_BY_CATEGORY = {
    'create_city_or_service_area_page':    'city_page',
    'create_service_page':                 'service_page',
    'faq_or_content_brief':                'faq',
    'improve_existing_page':               'service_page',
    'google_business_profile_opportunity': 'google_business_post',
}
_TYPE_BY_INTENT = {
    intent_mod.TRANSACTIONAL: 'service_page',
    intent_mod.COMMERCIAL:    'blog',
    intent_mod.INFORMATIONAL: 'faq',
    intent_mod.NAVIGATIONAL:  'google_business_post',
}


class NotApproved(RuntimeError):
    """Briefs come from approved recommendations only."""


def _profile():
    return config.load_marketing_profile()


def _page_type(rec, search_intent):
    by_cat = _TYPE_BY_CATEGORY.get(rec.get('category'))
    if rec.get('category') == 'faq_or_content_brief':
        return _TYPE_BY_INTENT.get(search_intent, 'faq')
    return by_cat or _TYPE_BY_INTENT.get(search_intent, 'blog')


def _outline(rec, page_type, city, service_label, question):
    """A skeleton, not prose. The writer fills it; the brief shapes it."""
    if page_type == 'city_page':
        return [
            f'H1: {service_label} in {city}, Colorado',
            f'What we do in {city} — the specific services offered there',
            f'Why {city} roofs fail — local conditions, stated only where we can '
            f'back them up',
            'What the process looks like, start to finish',
            'Proof: accreditations, licensing (see required assets)',
            'Service area — the surrounding towns we also cover',
            'FAQ — three to five real questions from this market',
            'Call to action',
        ]
    if page_type == 'service_page':
        return [
            f'H1: {service_label}',
            'What the work involves, in plain language',
            'How to tell you need it — observable signs, no scare tactics',
            'Materials and options we actually offer',
            'What the process looks like and how long it takes',
            'Proof: accreditations, licensing',
            'FAQ',
            'Call to action',
        ]
    if page_type in ('faq', 'blog'):
        return [
            f'H1: {question or rec.get("action", "")}',
            'The short answer, in the first paragraph — do not bury it',
            'The longer explanation',
            'What this means for a Colorado homeowner specifically',
            'When to call somebody',
            'Related questions',
            'Call to action',
        ]
    if page_type == 'google_business_post':
        return ['One paragraph, under 100 words',
                'A single specific detail — a job, a season, a real change',
                'One call to action']
    return ['Subject line', 'One idea per email', 'Single call to action']


def _internal_links(rec, pages, city, service_label):
    """Suggest links from pages we actually crawled — never invented URLs."""
    terms = [t.lower() for t in (city, service_label) if t]
    scored = []
    for p in pages or []:
        hay = (p.get('url', '') + ' ' + (p.get('title') or '')).lower()
        hits = sum(1 for t in terms if t and t in hay)
        if hits:
            scored.append((hits, p.get('url', ''), p.get('title') or p.get('url', '')))
    scored.sort(reverse=True)
    links = [{'url': u, 'label': t} for _h, u, t in scored[:5]]
    if not links and pages:
        links = [{'url': pages[0].get('url', ''),
                  'label': 'Homepage — no closely-related page found in the crawl'}]
    return links


def _required_assets(page_type, profile):
    """What must exist before this can publish. Drawn from the profile only."""
    creds = profile.get('credentials', {})
    accreditations = [a['label'] for a in creds.get('manufacturer_accreditations') or []]
    certs = [c['label'] for c in creds.get('certifications') or []]
    assets = [
        'At least one real photograph of our own work — no stock imagery of '
        'somebody else\'s roof',
    ]
    if page_type in ('city_page', 'service_page'):
        assets.append(
            'Proof block naming only these: '
            + ', '.join(accreditations + certs) + '. '
            + creds.get('licensing', {}).get('statement', ''))
        assets.append('A real local reference point for the city — a neighbourhood, '
                      'a landmark, a condition an actual resident would recognise')
    if page_type == 'faq':
        assets.append('Confirmation from a rep that customers genuinely ask this')
    return assets


def _claims_needing_review(profile):
    """The standing list. Short, because the profile keeps it short."""
    diffs = (profile.get('provable_differentiators') or {}).get('claims') or []
    out = [
        'Any statement about insurance coverage, claims handling, or '
        'deductibles — legal exposure, must be checked by someone who knows '
        'Colorado rules.',
        'Any statement about permits, code requirements, or licensing in a '
        'named jurisdiction.',
        'Any storm, hail or weather event referenced as fact — cite it or cut it.',
        'Any price, range, or financing term.',
    ]
    if not diffs:
        out.insert(0,
                   'NO COMPARATIVE OR SUPERIORITY CLAIMS. '
                   '`provable_differentiators` in the marketing profile is empty, '
                   'which means none are proven yet — not that you may use '
                   'judgement. No "best", "leading", "top-rated", "unmatched".')
    else:
        out.insert(0, 'Comparative claims are limited to these proven ones: '
                      + '; '.join(str(d.get('claim', '')) for d in diffs))
    banned = config.banned_phrases()
    if banned:
        out.append('Banned phrases from the marketing profile: '
                   + ', '.join(sorted(banned)) + '.')
    return out


_MEASUREMENT = {
    'measurable_today': [
        'The page exists and is reachable — the next weekly crawl confirms it.',
        'It is internally linked from a relevant page — the crawl confirms it.',
        'It carries a title, meta description, one H1 and valid structured data.',
        'It is not blocked by robots.txt or a noindex tag.',
    ],
    'not_measurable_today': [
        'Whether it ranks, and for what — needs Search Console.',
        'How much organic traffic it receives — needs Analytics.',
        'Whether it produced a lead — needs Analytics plus CRM attribution, and '
        'the CRM currently records every website lead as one undifferentiated '
        '"website" source.',
    ],
    'note': 'Judge this page on whether it answers the question well, not on '
            'numbers nobody can currently see. When Search Console or Analytics '
            'arrive, this section gets shorter.',
}


def build(rec, pages=None, profile=None):
    """Build a brief dict from one recommendation. Pure — no DB, no network."""
    profile = profile or _profile()
    question = (rec.get('intent') or '').strip()
    topic = question or rec.get('action', '')
    search_intent, intent_why = intent_mod.classify(topic)

    city = rec.get('city', '')
    service_key = rec.get('service', '')
    service_label = next(
        (c['label'] for c in profile['approved_services']['categories']
         if c['key'] == service_key), service_key or 'Roofing')

    page_type = _page_type(rec, search_intent)

    return {
        'recommendation_id': rec.get('id'),
        'topic': topic,
        'search_intent': search_intent,
        'search_intent_label': intent_mod.INTENT_LABEL[search_intent],
        'search_intent_reason': intent_why,
        'business_value': intent_mod.business_value(topic, search_intent,
                                                    city, service_key),
        'city': city,
        'service': service_key,
        'service_label': service_label,
        'customer_question': question or
            'Not captured on the source recommendation — confirm with a rep '
            'what customers actually ask before writing.',
        'page_type': page_type,
        'outline': _outline(rec, page_type, city, service_label, question),
        'internal_links': _internal_links(rec, pages, city, service_label),
        'required_assets': _required_assets(page_type, profile),
        'claims_needing_review': _claims_needing_review(profile),
        'call_to_action': _cta(page_type, city),
        'measurement': _MEASUREMENT,
        'evidence': rec.get('evidence') or [],
        'basis': 'Built from an approved recommendation. The topic ordering is '
                 'a judgement from public research — there is no search volume '
                 'behind it.',
    }


def _cta(page_type, city):
    where = f' in {city}' if city else ''
    if page_type == 'google_business_post':
        return 'One line, one action. "Call us" or "Request an inspection" — not both.'
    if page_type == 'email':
        return 'A single reply-friendly ask. This is 1:1 mail from a rep, not a blast.'
    return (f'Request a roof inspection{where}. Phone number visible without '
            f'scrolling, and a form that asks for as little as possible. '
            f'Do not describe the inspection using any banned phrase.')


# ── Persistence ──────────────────────────────────────────────────────────────

def create_for_recommendation(rec_id, pages=None):
    """Generate and save a brief. Refuses unless the recommendation is approved."""
    with config.get_cache_db() as db:
        row = db.execute('SELECT * FROM seo_recommendations WHERE id = ?',
                         (rec_id,)).fetchone()
    if not row:
        raise LookupError(f'no recommendation {rec_id}')
    rec = dict(row)
    if rec.get('status') != 'approved':
        raise NotApproved(
            f'recommendation {rec_id} is "{rec.get("status")}" — approve it '
            f'first. Briefs are for work somebody has signed off on.')
    try:
        rec['evidence'] = json.loads(rec.get('evidence') or '[]')
    except (ValueError, TypeError):
        rec['evidence'] = []

    brief = build(rec, pages=pages or _cached_pages())
    with config.get_cache_db() as db:
        cur = db.execute(
            'INSERT INTO seo_briefs (rec_id, created_at, topic, page_type, '
            'city, service, search_intent, brief_json, status) '
            'VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)',
            (rec_id, config.now_iso(), brief['topic'], brief['page_type'],
             brief['city'], brief['service'], brief['search_intent'],
             json.dumps(brief), 'draft'))
        db.commit()
        brief['id'] = cur.lastrowid
    return brief


def _cached_pages():
    """Pages from the last crawl, for internal-link suggestions."""
    with config.get_cache_db() as db:
        rows = db.execute('SELECT url, extracted FROM seo_page_cache '
                          'ORDER BY fetched_at DESC LIMIT 200').fetchall()
    pages = []
    for r in rows:
        try:
            data = json.loads(r['extracted'] or '{}')
        except (ValueError, TypeError):
            data = {}
        data['url'] = r['url']
        pages.append(data)
    return pages


def get(brief_id):
    with config.get_cache_db() as db:
        row = db.execute('SELECT * FROM seo_briefs WHERE id = ?',
                         (brief_id,)).fetchone()
    if not row:
        return None
    out = dict(row)
    try:
        out['brief'] = json.loads(out.get('brief_json') or '{}')
    except (ValueError, TypeError):
        out['brief'] = {}
    out.pop('brief_json', None)
    return out


def list_briefs(limit=100):
    with config.get_cache_db() as db:
        rows = db.execute(
            'SELECT id, rec_id, created_at, topic, page_type, city, service, '
            'search_intent, status FROM seo_briefs ORDER BY id DESC LIMIT ?',
            (limit,)).fetchall()
    return [dict(r) for r in rows]


def to_markdown(brief):
    """The form a human actually hands to a writer."""
    b = brief
    lines = [f'# Content brief — {b["topic"]}', '',
             f'- **Page type:** {b["page_type"].replace("_", " ")}',
             f'- **Search intent:** {b["search_intent_label"]} '
             f'({b["search_intent_reason"]})',
             f'- **Business value:** {b["business_value"]}/10 — a judgement, not a '
             f'measurement',
             f'- **Target area:** {b.get("city") or "—"}',
             f'- **Service:** {b.get("service_label") or "—"}', '',
             '## Customer question being answered', '',
             b['customer_question'], '',
             '## Outline', '']
    lines += [f'{i}. {step}' for i, step in enumerate(b['outline'], 1)]
    lines += ['', '## Internal links to include', '']
    lines += [f'- [{l["label"]}]({l["url"]})' for l in b['internal_links']] or ['- _none suggested_']
    lines += ['', '## Required proof and assets', '']
    lines += [f'- {a}' for a in b['required_assets']]
    lines += ['', '## Claims requiring review before publish', '']
    lines += [f'- {c}' for c in b['claims_needing_review']]
    lines += ['', '## Call to action', '', b['call_to_action'], '',
              '## How success will be measured', '',
              '**Measurable today:**', '']
    lines += [f'- {m}' for m in b['measurement']['measurable_today']]
    lines += ['', '**Not measurable today:**', '']
    lines += [f'- {m}' for m in b['measurement']['not_measurable_today']]
    lines += ['', f'_{b["measurement"]["note"]}_']
    if b.get('evidence'):
        lines += ['', '## Sources', ''] + [f'- {u}' for u in b['evidence'][:8]]
    return '\n'.join(lines)
