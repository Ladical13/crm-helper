"""GDELT Global Knowledge Graph — free local news feed. Skeleton for v1.

Wiring:
    Query GDELT's DOC 2.0 API:
      https://api.gdeltproject.org/api/v2/doc/doc?query=...&mode=artlist&format=json
    Filter to Colorado (`sourcecountry:USA sourcelocation:'Colorado'`) and
    keywords in KEYWORDS. Deduplicate by URL, return the last 7 days.
"""

KEYWORDS = ['roof', 'roofing', 'hail', 'storm damage', 'insurance dispute']


def pull(days=7):
    return []
