"""IRS Exempt Organizations Business Master File — the free churches source.

The IRS publishes a CSV per state with every 501(c)(3) organization, including
their address and NTEE code. Churches are NTEE code X20/X21 and denomination
codes 'religious' — the file is authoritative and free.

v1 is a skeleton returning an empty list; the dispatcher then falls through to
``perplexity_gap.pull`` so churches still get populated. Wire the real CSV
parser here when you're ready to trade Perplexity's per-lead cost for a free
one-time download.

Wiring the real thing:
    URL: https://www.irs.gov/charities-non-profits/exempt-organizations-business-master-file-extract-eo-bmf
    Download the state file (eo_co.csv for Colorado), filter NTEE ∈ {X20, X21, X22},
    normalize into agents.b2b.sources.perplexity_gap._normalize()'s output shape.
"""


def churches(city='', county='', state='CO', limit=None):
    """Placeholder — returns [] so the dispatcher falls through to Perplexity.

    Once the IRS BMF is wired, this should return rows shaped like
    prospector/normalize.py FIELDS with lead_type=church implicit.
    """
    return []
