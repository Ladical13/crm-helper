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
from . import assessor, irs_bmf, nces, cdle, perplexity_gap


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
    # The account above the schools. Free, and it carries the administration
    # office's phone — the number a rep should be dialling anyway.
    'school_district': [nces.districts],
    'gc':         [cdle.contractors, perplexity_gap.pull],
    # County assessor records: who owns the building is public record in both
    # counties we work. Perplexity stays as the fallback for a city neither
    # county covers.
    'commercial': [assessor.commercial, perplexity_gap.pull],
}


def pullers_for(segment):
    """Return the ordered list of source functions for a segment.

    An unknown segment returns Perplexity as the only puller — worst-case
    the model returns nothing and we log it.
    """
    return SEGMENT_SOURCES.get(segment) or [perplexity_gap.pull]


# What a person picks from in Nimbus: the segment checkboxes on a rep's page
# and the category picker beside RUN. Every key here is also a salescrm lead
# type — the import rejects any other, which is why 'brokerage' (a prospector
# segment with no lead type of its own) is not offered.
SEGMENT_LABELS = {
    'church':           'Churches',
    'school':           'Schools',
    'school_district':  'School districts',
    'gc':               'General contractors',
    'commercial':       'Commercial buildings',
    'realtor':          'Realtors',
    'insurance_agent':  'Insurance agents',
    'hoa':              'HOAs',
    'property_manager': 'Property managers',
}


def segment_catalog():
    """[{key, label, searches}] in display order.

    ``searches`` is False for a segment with no puller: a run over it finds
    nothing new, because those partners arrive through the offline prospector.
    The page says so next to the checkbox rather than letting a run come back
    empty with no explanation.
    """
    return [{'key': k, 'label': v, 'searches': bool(SEGMENT_SOURCES.get(k))}
            for k, v in SEGMENT_LABELS.items()]
