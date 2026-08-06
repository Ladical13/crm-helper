"""Google Trends via pytrends. Free, unofficial. Skeleton for v1.

Wiring:
    from pytrends.request import TrendReq
    py = TrendReq(hl='en-US', tz=360)
    py.build_payload(KWS, geo='US-CO', timeframe='now 7-d')
    return py.related_queries()['rising'] and py.interest_over_time()
"""

RISING_KEYWORDS = [
    'roof replacement', 'hail damage claim', 'roofer near me',
    'storm damage', 'insurance adjuster', 'roof leak',
    'roof insurance claim',
]


def pull():
    return []
