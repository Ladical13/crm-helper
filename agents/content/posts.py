"""Social + Google Business Profile post bot.

Takes a topic that earned its place — an approved SEO recommendation, a real
Colorado storm from GDELT, a question homeowners are actually asking on Reddit
— and writes one post per platform as a **package**, so a human reviews the
set together rather than four disconnected drafts.

**Draft-only, and that is a decision rather than a limitation.** Publishing to
Facebook, Instagram or LinkedIn needs app review on each platform; Google
Business Profile needs the API approval that is still pending. But even with
all four wired, auto-posting would be crossing the line this whole system is
built around: nothing reaches a customer without a person seeing it first.
The handoff is a copy button and a link to the platform.

Every post goes through the same honesty guard as an SEO recommendation. A
social post claiming "the #1 roofer in Fort Collins" is the same fabrication
as a report claiming it, and gets rejected the same way.
"""
import json
import uuid

from .. import config, perplexity
from ..seo import honesty

# Per-platform voice, length and hard limits. Lengths are measurable targets
# because a model tightens reliably against "100-150 words" and not at all
# against "short".
PLATFORMS = {
    'facebook': {
        'label': 'Facebook',
        'words': '80-130',
        'voice': 'conversational, like a neighbour who does this for a living. '
                 'One soft call to action. No emoji spam, no hard sell.',
        'hard_limit': 2000,
        'link': 'https://www.facebook.com/',
    },
    'instagram': {
        'label': 'Instagram',
        'words': '50-90',
        'voice': 'tight and direct, written to be read under a photo. '
                 '6-10 relevant hashtags, local ones included.',
        'hard_limit': 2200,
        'needs_image': True,
        'link': 'https://www.instagram.com/',
    },
    'linkedin': {
        'label': 'LinkedIn',
        'words': '120-200',
        'voice': 'professional and specific. Written for property managers, '
                 'HOA boards and commercial owners rather than homeowners.',
        'hard_limit': 3000,
        'link': 'https://www.linkedin.com/feed/',
    },
    'google_business': {
        'label': 'Google Business Profile',
        'words': '60-100',
        'voice': 'plain and factual, the way a business update reads. One '
                 'specific detail and one call to action. No hashtags — they '
                 'do nothing here. No phone numbers in the body.',
        # Google's own limit is 1500 characters; short posts perform better and
        # the summary is what shows before the fold.
        'hard_limit': 1500,
        'link': 'https://business.google.com/posts',
    },
}

DEFAULT_PLATFORMS = ('facebook', 'instagram', 'linkedin', 'google_business')


class PostsUnavailable(RuntimeError):
    """No writer available — missing key or the spend cap is exhausted."""


def _system_prompt(profile):
    services = ', '.join(c['label'] for c in
                         profile['approved_services']['categories'])
    creds = profile.get('credentials', {})
    accreditations = ', '.join(
        [a['label'] for a in creds.get('manufacturer_accreditations') or []]
        + [c['label'] for c in creds.get('certifications') or []])
    banned = ', '.join(sorted(config.banned_phrases())) or 'none'
    diffs = (profile.get('provable_differentiators') or {}).get('claims') or []

    lines = [
        'You write social posts for a Colorado roofing and exterior contractor.',
        f'Approved services, and NOTHING else: {services}.',
        'Service area: Colorado east of the mountains, focused on Northern '
        'Colorado and the Colorado Springs area.',
        f'Accreditations you may name, spelled exactly: {accreditations}.',
        '',
        'Absolute rules:',
        '- NEVER state a ranking, review count, star rating, number of jobs, '
        'years in business, search volume or traffic figure. You do not have '
        'that data.',
        '- NEVER invent a customer, a testimonial, a price, a storm, or an '
        'insurance rule. If it is not in the brief, leave it out.',
        f'- NEVER use these phrases: {banned}.',
        '- Do not promise a timeline, a discount, or an insurance outcome.',
    ]
    if diffs:
        lines.append('- The only comparative claims allowed: '
                     + '; '.join(str(d.get('claim', '')) for d in diffs))
    else:
        lines.append('- NO comparative or superiority claims at all. Not "best", '
                     '"top-rated", "leading", "trusted by thousands". None are '
                     'proven, so none may be written.')
    lines += [
        '',
        'Write like a person who has been on a roof. Plain, specific, unhyped.',
    ]
    return '\n'.join(lines)


def _prompt(topic, platform, spec, context=''):
    extras = ''
    if spec.get('needs_image'):
        extras = ('Also return "image_prompt": a description of the photograph '
                  'that should accompany this — a real photo we would take on '
                  'a job, not stock imagery. ')
    return (
        f'Write ONE {spec["label"]} post.\n\n'
        f'Topic: {topic.get("topic", "")}\n'
        f'Context: {topic.get("summary", "") or context}\n'
        f'City / area: {topic.get("city", "") or "Northern Colorado"}\n\n'
        f'Length: {spec["words"]} words. Voice: {spec["voice"]}\n\n'
        f'{extras}'
        f'Return JSON: {{"body": "...", "hashtags": ["#..."], '
        f'"call_to_action": "...", "image_prompt": "..."}}\n'
        f'Use an empty list for hashtags where they do not belong.'
    )


def _render(data, platform):
    """Flatten the model's JSON into copy-paste-ready text."""
    if not isinstance(data, dict):
        return str(data or '')
    parts = []
    if data.get('body'):
        parts.append(str(data['body']).strip())
    if platform == 'instagram' and data.get('image_prompt'):
        parts += ['', f'[Photo to shoot: {data["image_prompt"]}]']
    if data.get('call_to_action'):
        parts += ['', str(data['call_to_action']).strip()]
    tags = data.get('hashtags')
    if platform != 'google_business' and isinstance(tags, list) and tags:
        parts += ['', ' '.join(str(t) for t in tags)]
    return '\n'.join(parts).strip() or json.dumps(data, indent=2)


def _vet(text, platform, spec):
    """Reject a post rather than publish a claim we cannot stand behind.

    Same guard the SEO recommendations use — a social post asserting a ranking
    is the same fabrication as a report asserting one.
    """
    problems = []
    fabricated = honesty.find_fabricated_metrics(text)
    if fabricated:
        problems.append(f'unsupportable claim: {fabricated[0][0]!r} '
                        f'({fabricated[0][1]})')
    banned = honesty.find_banned_phrases(text)
    if banned:
        problems.append(f'banned phrase(s): {", ".join(banned)}')
    if len(text) > spec['hard_limit']:
        problems.append(f'{len(text)} characters exceeds the {spec["label"]} '
                        f'limit of {spec["hard_limit"]}')
    if not text.strip():
        problems.append('empty post')
    return problems


def _review_notes(platform, topic):
    notes = ['Read it as a customer before it goes anywhere.']
    if platform == 'instagram':
        notes.append('Needs a real photo of our own work — not stock.')
    if platform == 'google_business':
        notes.append('Google rejects posts with phone numbers in the body and '
                     'anything reading as an unverifiable offer.')
    if any('reddit.com' in str(c) for c in (topic.get('citations') or [])):
        notes.append('Sourced from a Reddit thread — do NOT quote it, link it, '
                     'or make the original poster identifiable.')
    if topic.get('source') == 'gdelt':
        notes.append('References a real news event — check the article still '
                     'says what we think it says before posting.')
    return ' '.join(notes)


def build_package(topic, platforms=DEFAULT_PLATFORMS, model=None, dry_run=False):
    """Write one post per platform for a single topic.

    Returns ``{package_id, posts, rejected, cost_usd}``. Never raises for a
    single platform failing — the others still ship.
    """
    profile = config.load_marketing_profile()
    system = _system_prompt(profile)
    package_id = uuid.uuid4().hex[:12]
    posts, rejected = [], []
    cost = 0.0

    for platform in platforms:
        spec = PLATFORMS.get(platform)
        if not spec:
            rejected.append({'platform': platform, 'reason': 'unknown platform'})
            continue
        try:
            r = perplexity.search_json(_prompt(topic, platform, spec),
                                       system=system, model=model,
                                       max_tokens=900,
                                       reason=f'social-post:{platform}')
        except perplexity.SpendCapReached as e:
            rejected.append({'platform': platform, 'reason': f'spend cap: {e}'})
            continue
        except perplexity.PerplexityError as e:
            rejected.append({'platform': platform, 'reason': str(e)})
            continue
        cost += float(r.get('cost_usd') or 0.0)

        text = _render(r.get('data') or {}, platform)
        problems = _vet(text, platform, spec)
        if problems:
            # Dropped, not softened. Editing a hallucinated number out leaves
            # the reasoning around it intact and still wrong.
            rejected.append({'platform': platform, 'reason': '; '.join(problems)})
            continue

        posts.append({
            'package_id': package_id,
            'platform': platform,
            'topic': topic.get('topic', '')[:200],
            'draft_text': text,
            'citations': topic.get('citations') or [],
            'source': topic.get('source', ''),
            'review_notes': _review_notes(platform, topic),
        })

    if not dry_run and posts:
        _persist(posts)
    return {'package_id': package_id, 'posts': posts, 'rejected': rejected,
            'cost_usd': round(cost, 4)}


def _persist(posts):
    with config.get_cache_db() as db:
        for p in posts:
            cur = db.execute(
                'INSERT INTO content_drafts (created_at, platform, topic, '
                'draft_text, citations, status, package_id, source, review_notes) '
                'VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)',
                (config.now_iso(), p['platform'], p['topic'], p['draft_text'],
                 json.dumps(p['citations']), 'draft', p['package_id'],
                 p['source'], p['review_notes']))
            p['id'] = cur.lastrowid
        db.commit()


# ── Choosing what to post about ─────────────────────────────────────────────

def candidate_topics(limit=3):
    """What is worth posting about this week, best first.

    Ordered by how well-earned the topic is rather than by recency: an
    approved recommendation has a human behind it, a real storm is timely and
    citable, and a trending topic is merely interesting.
    """
    topics = []

    # 1. Approved SEO recommendations — somebody signed off on these.
    with config.get_cache_db() as db:
        rows = db.execute(
            "SELECT id, action, city, service, intent, evidence FROM "
            "seo_recommendations WHERE status = 'approved' "
            "ORDER BY score DESC LIMIT ?", (limit,)).fetchall()
    for r in rows:
        try:
            citations = json.loads(r['evidence'] or '[]')
        except (ValueError, TypeError):
            citations = []
        topics.append({
            'topic': r['intent'] or r['action'],
            'summary': f'Approved SEO recommendation for {r["city"] or "our area"}.',
            'city': r['city'], 'citations': citations,
            'source': 'seo_recommendation', 'rec_id': r['id'],
        })

    # 2. A real, dated Colorado storm — the one thing we may cite as fact.
    if len(topics) < limit:
        from .sources import news_gdelt
        storms = news_gdelt.storm_events(days=21, limit=5)
        for a in (storms.get('articles') or [])[:limit - len(topics)]:
            topics.append({
                'topic': a['title'],
                'summary': f'Reported by {a["domain"]}'
                           + (f' on {a["seen_at"][:10]}' if a.get('seen_at') else ''),
                'city': '', 'citations': [a['url']], 'source': 'gdelt',
            })

    # 3. Whatever the listeners ranked highest.
    if len(topics) < limit:
        with config.get_cache_db() as db:
            rows = db.execute(
                'SELECT topic, summary, sources FROM trending_topics '
                'ORDER BY score DESC, id DESC LIMIT ?',
                (limit - len(topics),)).fetchall()
        for r in rows:
            try:
                citations = json.loads(r['sources'] or '[]')
            except (ValueError, TypeError):
                citations = []
            topics.append({'topic': r['topic'], 'summary': r['summary'],
                           'city': '', 'citations': citations,
                           'source': 'trending'})
    return topics[:limit]


def weekly_run(max_topics=2, platforms=DEFAULT_PLATFORMS, dry_run=False):
    """The scheduled pass: a couple of topics, one package each.

    Two, not ten. The whole point of a queue is that a person reads it, and
    nobody reads forty drafts.
    """
    topics = candidate_topics(limit=max_topics)
    if not topics:
        return {'packages': [], 'cost_usd': 0.0,
                'note': 'nothing worth posting about — no approved '
                        'recommendations, no recent Colorado storm, no topics'}
    packages, cost = [], 0.0
    for t in topics:
        pkg = build_package(t, platforms=platforms, dry_run=dry_run)
        cost += pkg['cost_usd']
        packages.append(pkg)
    made = sum(len(p['posts']) for p in packages)
    dropped = sum(len(p['rejected']) for p in packages)
    return {'packages': packages, 'cost_usd': round(cost, 4),
            'note': f'{made} draft(s) across {len(packages)} topic(s)'
                    + (f', {dropped} rejected by the honesty check' if dropped else '')}


def list_drafts(status='draft', limit=100):
    with config.get_cache_db() as db:
        where, params = ('WHERE status = ?', [status]) if status else ('', [])
        rows = db.execute(
            f'SELECT id, created_at, platform, topic, draft_text, citations, '
            f'status, package_id, source, review_notes, approved_by, posted_at '
            f'FROM content_drafts {where} ORDER BY id DESC LIMIT ?',
            params + [limit]).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        try:
            d['citations'] = json.loads(d.get('citations') or '[]')
        except (ValueError, TypeError):
            d['citations'] = []
        d['platform_label'] = PLATFORMS.get(d['platform'], {}).get(
            'label', d['platform'])
        d['platform_link'] = PLATFORMS.get(d['platform'], {}).get('link', '')
        out.append(d)
    return out
