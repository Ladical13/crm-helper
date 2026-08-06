"""YouTube Data API v3. Free tier (10k units/day). Skeleton for v1.

Wiring:
    key = os.environ['YOUTUBE_API_KEY']
    # search.list for roofing/hail/insurance videos in CO markets,
    # commentThreads.list to mine top comments per video for pain points.
"""
import os


def available():
    return bool(os.environ.get('YOUTUBE_API_KEY'))


def pull(limit=50):
    return []
