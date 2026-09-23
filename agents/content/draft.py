"""Compatibility entry point; all channels use the brand-aware package writer."""
from . import posts

DEFAULT_PLATFORMS = posts.DEFAULT_PLATFORMS


def draft_topic(topic, platforms=DEFAULT_PLATFORMS, model=None):
    package = posts.build_package(topic, platforms=platforms, model=model)
    return {'drafts': [{'platform': p['platform'], 'draft_id': p['id'],
                        'preview': p['draft_text'][:200]} for p in package['posts']],
            'rejected': package['rejected'], 'cost_usd': package['cost_usd']}
