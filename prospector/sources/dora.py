"""DORA licensee roster — Colorado Division of Real Estate.

Socrata `4zse-6bnw`, free and keyless, ~109k rows statewide. It carries name,
city, zip and licence, and **no email, phone or street address** — that gap is
the whole reason this engine is company-first. Reaching a company is free
because CDOS has its address and companies publish their own contact details;
reaching 49k individual brokers is what would have cost money.

Segment sizes at time of writing (all statuses, all states):

    Home Owner's Association          8,588
    HOA Designated Agent              7,630
    Real Estate Company - Employing   7,927
    Real Estate Company - Independent 8,813
    individual broker types          ~49,400   (excluded by default — see below)
"""
from .. import normalize, socrata

DATASET = '4zse-6bnw'

_BROKER_TYPES = [
    'Associate Level Real Estate Broker',
    'Employing Level Real Estate Broker - Responsible Broker',
    'Employing Level Real Estate Broker - Associate',
    'Employing Level Real Estate Broker - Individual Proprietor',
    'Independent Level Real Estate Broker - Individual Proprietor',
    'Independent Level Real Estate Broker - Associate',
    'Independent Level Real Estate Broker - Responsible Broker',
]

SEGMENTS = {
    'hoa': {
        'label': 'Registered HOAs',
        'lead_type': 'hoa',
        'types': ["Home Owner's Association"],
        'note': 'Direct commercial buyers, not just referrers. Start here.',
    },
    'hoa_agent': {
        'label': 'HOA designated agents',
        'lead_type': 'property_manager',
        'types': ['HOA Designated Agent'],
        'note': "CCIOA-required public contact for an association - usually its "
                "management company.",
    },
    'brokerage': {
        'label': 'Real estate brokerages',
        'lead_type': 'realtor',
        'types': ['Real Estate Company - Employing', 'Real Estate Company - Independent'],
        'note': 'Office-level. One visit reaches every agent in the branch.',
    },
    'broker': {
        'label': 'Individual brokers',
        'lead_type': 'realtor',
        'types': _BROKER_TYPES,
        'note': 'EXCLUDED from the free tier: no contact details in the dataset, '
                'so every one of these needs paid enrichment. Work the brokerages '
                'instead.',
        'default': False,
    },
}


def where(segment, active_only=True, state='CO'):
    clause = [socrata.any_of('licensetype', SEGMENTS[segment]['types'])]
    if active_only:
        clause.append(f'licensestatus = {socrata.quote("Active")}')
    if state:
        clause.append(f'state = {socrata.quote(state)}')
    return ' AND '.join(clause)


def count(segment, **kw):
    return socrata.count(DATASET, where=where(segment, **kw))


def pull(segment, limit=None, active_only=True, state='CO'):
    """Yield normalized prospect rows for one segment."""
    if segment not in SEGMENTS:
        raise KeyError(f'unknown DORA segment {segment!r}')
    seen = set()
    for r in socrata.fetch(DATASET, where=where(segment, active_only, state), limit=limit):
        licence = (r.get('licensenumber') or '').strip()
        company = normalize.clean_entity_name(r.get('entityname') or '')
        first   = (r.get('firstname') or '').strip()
        last    = (r.get('lastname') or '').strip()
        if not (company or first or last) or not licence:
            continue
        # One licensee can hold several credentials; the importer would catch
        # the repeat, but there is no reason to ship it.
        if licence in seen:
            continue
        seen.add(licence)
        city = (r.get('city') or '').strip()
        yield normalize.row(
            first_name=first, last_name=last, company=company,
            city=city, state=(r.get('state') or '').strip(),
            zip=(r.get('zipcode') or '').strip(),
            license_no=licence,
            source_ref=f'dora:{DATASET}:{licence}',
            icp_score=normalize.score(company=company, city=city,
                                      person=(first or last), active=True),
        )
