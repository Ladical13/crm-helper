"""NCES ELSI — public school directory. FREE. Placeholder for v1.

Wiring the real thing:
    URL: https://nces.ed.gov/ccd/elsi/tableGenerator.aspx
    Or the API at https://educationdata.urban.org/api-v1/
    Filter by state=CO + type=1..8 (regular schools). Normalize into the
    prospector shape. Also drop CO Dept of Education open data
    (data.colorado.gov -> datasets 'Schools' etc.) here for K-12 private.
"""


def schools(city='', county='', state='CO', limit=None):
    """Placeholder — falls through to Perplexity gap-fill."""
    return []
