"""Turn a ranked topic into platform-tailored post drafts.

Every draft is saved to ``content_drafts`` in Nimbus's DB with citations
attached, then the reviewer picks up in the Nimbus dashboard. Drafts only,
never auto-post — matches the sales-CRM outreach philosophy (rep sends from
their own account).
"""
import json

from .. import config, perplexity


# Platform-specific voice + length targets. Keep these short — Perplexity
# tightens more reliably when the target is measurable ("100-150 words") than
# when it's vague ("short").
_PLATFORMS = {
    'facebook':  {'words': '100-150', 'voice': 'conversational, one soft CTA, no emoji spam',
                  'hook':  'a one-line hook that stops the scroll'},
    'instagram': {'words': '60-100', 'voice': 'tight, direct, add 6-10 relevant hashtags',
                  'hook':  'a punchy one-line hook + image prompt'},
    'linkedin':  {'words': '200-300', 'voice': 'professional, industry-insight framing',
                  'hook':  'a business-owner insight worth the click'},
    'blog':      {'words': '600-900', 'voice': 'SEO-friendly, subheads, real how-to detail',
                  'hook':  'a headline + a 1-paragraph intro'},
}

DEFAULT_PLATFORMS = ('facebook', 'instagram', 'linkedin')


_SYSTEM = (
    'You are a marketing copywriter for a Colorado roofing contractor. Voice '
    'is plain, honest, and non-hype. Never invent testimonials or statistics. '
    'Every claim must trace back to one of the provided source URLs; if it '
    'doesn\'t, leave it out. Never write "just checking in" or "circling '
    'back". If the topic doesn\'t fit the platform, say so instead of writing '
    'anything.'
)


def _prompt(topic, platform):
    spec = _PLATFORMS.get(platform, _PLATFORMS['facebook'])
    cites = topic.get('citations') or []
    cite_block = '\n'.join(f'  - {c}' for c in cites) if cites else '  (none provided)'
    return (
        f'Draft ONE {platform.title()} post about the following trending '
        f'topic. Length: {spec["words"]} words. Voice: {spec["voice"]}. '
        f'Start with: {spec["hook"]}.\n\n'
        f'Topic: {topic.get("topic", "")}\n'
        f'Summary: {topic.get("summary", "")}\n'
        f'Why now: {topic.get("why_now", "")}\n'
        f'Audience: {topic.get("audience", "")}\n\n'
        f'Cite from ONLY these sources:\n{cite_block}\n\n'
        f'Return JSON: '
        f'{{ "headline": "...", "body": "...", "hashtags": ["#..."], '
        f'"image_prompt": "..." (Instagram only), '
        f'"call_to_action": "..." }}'
    )


def draft_topic(topic, platforms=DEFAULT_PLATFORMS, model=None):
    """Draft posts for each platform. Persist to the drafts table."""
    saved = []
    total_cost = 0.0
    for platform in platforms:
        try:
            r = perplexity.search_json(_prompt(topic, platform),
                                       system=_SYSTEM, model=model,
                                       max_tokens=1200,
                                       reason=f'content-draft:{platform}')
        except perplexity.SpendCapReached:
            saved.append({'platform': platform, 'status': 'skipped-cap'})
            continue
        total_cost += float(r.get('cost_usd') or 0.0)
        data = r.get('data') or {}
        draft_text = _render_draft(data, platform)
        citations = topic.get('citations') or (r.get('citations') or [])
        # Persist.
        with config.get_cache_db() as db:
            cur = db.execute(
                'INSERT INTO content_drafts (created_at, platform, topic, '
                'draft_text, citations, status) VALUES (?, ?, ?, ?, ?, ?)',
                (config.now_iso(), platform, topic.get('topic', '')[:200],
                 draft_text, json.dumps(citations), 'draft'))
            saved.append({'platform': platform, 'draft_id': cur.lastrowid,
                          'preview': draft_text[:200]})
            db.commit()
    return {'drafts': saved, 'cost_usd': round(total_cost, 4)}


def _render_draft(data, platform):
    """Flatten Perplexity's JSON into a copy/paste-ready block."""
    if not isinstance(data, dict):
        return str(data or '')
    parts = []
    if data.get('headline'):
        parts.append(str(data['headline']))
        parts.append('')
    if data.get('body'):
        parts.append(str(data['body']))
    if platform == 'instagram' and data.get('image_prompt'):
        parts.append('')
        parts.append(f'[Image idea: {data["image_prompt"]}]')
    if data.get('hashtags'):
        tags = data['hashtags']
        if isinstance(tags, list):
            parts.append('')
            parts.append(' '.join(tags))
    if data.get('call_to_action'):
        parts.append('')
        parts.append(str(data['call_to_action']))
    return '\n'.join(parts).strip() or json.dumps(data, indent=2)
