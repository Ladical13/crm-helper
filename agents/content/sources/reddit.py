"""Reddit listening via PRAW. Free. Requires REDDIT_CLIENT_ID/SECRET.

v1 ships as a skeleton so Nimbus works without a Reddit app registered;
the marketing agent still produces topics from Perplexity synthesis while
you wire this up. Add ``praw>=7.7`` to requirements.txt and set the env
vars to activate.

Wiring:
    reddit = praw.Reddit(client_id=..., client_secret=..., user_agent='p1-nimbus')
    for sub in TARGET_SUBS:
        for post in reddit.subreddit(sub).top('week', limit=25):
            if any(kw in post.title.lower() for kw in KEYWORDS):
                yield {...}
"""
import os

TARGET_SUBS = [
    'HomeImprovement', 'Roofing',
    'Denver', 'coloradosprings', 'FortCollins', 'Colorado',
    'Insurance', 'HOA', 'RealEstate', 'ColoradoRealEstate',
    'HouseFlipping', 'HomeOwners',
]

KEYWORDS = [
    'roof', 'roofing', 'roofer', 'shingle', 'hail', 'storm', 'leak',
    'insurance claim', 'adjuster', 'depreciation', 'deductible',
    'contractor', 'quote', 'estimate', 'HOA roof',
]


def available():
    return bool(os.environ.get('REDDIT_CLIENT_ID')
                and os.environ.get('REDDIT_CLIENT_SECRET'))


def pull(limit=100):
    """Return [] until PRAW is wired up. See module docstring."""
    return []
