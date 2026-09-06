"""Why a deal was lost — one vocabulary, shared by both halves of the funnel.

The estimator owned this list and the CRM took free text from a browser
`prompt()`, so the two could never be added together. That mattered more than
it looks: an estimate can only be lost after one has been built, and most
deals die earlier than that — at the door, on the phone, before anyone priced
anything. The CRM holds the larger and more actionable half of the answer to
"why do we lose", and it was holding it as unaggregatable prose.

Lives in `portal/` for the same reason `funnel.py` and `geo.py` do: the apps
keep separate databases, so anything genuinely shared needs a home belonging
to none of them.

Adding a reason is additive and safe. **Renaming a key is not** — keys are
stored on estimates and leads on a live volume, and a rename orphans every
record already carrying the old one. Change the label; leave the key alone.
"""

REASONS = {
    'price':        'Price — we were too expensive',
    'competitor':   'Went with another contractor',
    'timing':       'Not doing it now / postponed',
    'insurance':    'Insurance denied or underpaid the claim',
    'unresponsive': 'Went quiet — never got an answer',
    'scope':        'Changed their mind on the work',
    'other':        'Other',
}

# Losses recorded before there was a picker. Counted as their own bucket rather
# than dropped, which would inflate the share of every reason that IS recorded.
UNRECORDED = 'unrecorded'


def label(key):
    return REASONS.get(key, REASONS['other'] if key else '')


def valid(key):
    """'' is valid: not every loss has a reason yet, and refusing the move
    would leave a dead deal sitting in an open stage instead."""
    return key == '' or key in REASONS
