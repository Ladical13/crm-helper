"""Segment-to-source mapping.

Free sources are tried first; Perplexity gap-fill only fires when a segment
has no free option or the free one returned nothing for a given county.

Adding a new segment:
  1. Extend LEAD_TYPES in salescrm/app.py:78-95 and add outreach templates
     in salescrm/outreach_templates.json.
  2. Register it here with a list of pullers (free_first, then perplexity).
  3. Each puller is a function returning a list of dicts that match the
     salescrm importer's shape (see prospector/normalize.py FIELDS).
"""
from . import irs_bmf, nces, cdle, perplexity_gap


# Order matters: free sources first, Perplexity last. Each function has the
# signature (city, county, state, limit) -> [row, ...].
SEGMENT_SOURCES = {
    # Segments already covered by the free open-data prospector — Nimbus only
    # runs a Perplexity enrichment pass on the top-N, no new pull needed.
    'realtor':          [],
    'brokerage':        [],
    'insurance_agent':  [],
    'hoa':              [],
    'property_manager': [],

    # New segments that need Nimbus.
    'church':     [irs_bmf.churches, perplexity_gap.pull],
    'school':     [nces.schools, perplexity_gap.pull],
    'gc':         [cdle.contractors, perplexity_gap.pull],
    'commercial': [perplexity_gap.pull],
}


def pullers_for(segment):
    """Return the ordered list of source functions for a segment.

    An unknown segment returns Perplexity as the only puller — worst-case
    the model returns nothing and we log it.
    """
    return SEGMENT_SOURCES.get(segment) or [perplexity_gap.pull]
