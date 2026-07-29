"""Colorado business entity registry — Secretary of State.

Socrata `4ykn-tg5h`, free and keyless. This is the source that makes the free
tier work: unlike DORA it carries a **principal street address** and a
**registered agent name**, so every row is callable and droppable without any
paid enrichment.

Verified: 2,094 entities matching "PROPERTY MANAGEMENT" in Good Standing.

Two traps this module handles:

  • Lapsed entities carry their status inside the name itself, e.g. "ARG
    PROPERTY MANAGEMENT CORPORATION, Delinquent September 1, 2009". Filtering to
    Good Standing avoids most of it; normalize.clean_entity_name catches the rest.
  • The registered agent is sometimes a person (agentfirstname/agentlastname)
    and sometimes a corporate service (agentorganizationname, e.g. "CSC"). A
    registered-agent service is not a roofing contact, so those are not treated
    as a named person.
"""
from .. import normalize, socrata

DATASET = '4ykn-tg5h'

# Registered-agent services — a filing address, never a prospect.
_AGENT_SERVICES = {'csc', 'ct corporation', 'corporation service company',
                   'registered agents inc', 'northwest registered agent',
                   'incorp services', 'national registered agents',
                   'cogency global', 'legalzoom', 'harbor compliance'}

SEGMENTS = {
    'property_manager': {
        'label': 'Property management companies',
        'lead_type': 'property_manager',
        'keywords': ['PROPERTY MANAGEMENT', 'PROPERTY MGMT'],
        'note': 'Verified ~2,094 in Good Standing. Highest value per contact.',
    },
    'hoa_manager': {
        'label': 'HOA / community association managers',
        'lead_type': 'property_manager',
        'keywords': ['COMMUNITY ASSOCIATION', 'COMMUNITY MANAGEMENT', 'HOA MANAGEMENT'],
        'note': 'Pairs with the dora hoa_agent segment.',
    },
    'insurance_agent': {
        'label': 'Insurance agencies',
        'lead_type': 'insurance_agent',
        'keywords': ['INSURANCE AGENCY', 'INSURANCE AGENCIES', 'INSURANCE GROUP',
                     'INSURANCE SERVICES'],
        'note': 'The weakest segment - Colorado publishes no free bulk producer '
                'list, so this name-matching is the only free path to agencies.',
    },
    'realty': {
        'label': 'Realty companies',
        'lead_type': 'realtor',
        'keywords': ['REALTY', 'REAL ESTATE'],
        'note': 'Overlaps the DORA brokerage segment, but adds street addresses.',
    },
}


def where(segment, state='CO', good_standing=True):
    kws = SEGMENTS[segment]['keywords']
    name = ' OR '.join(f'upper(entityname) like {socrata.quote(f"%{k}%")}' for k in kws)
    clause = [f'({name})']
    if good_standing:
        clause.append(f'entitystatus = {socrata.quote("Good Standing")}')
    if state:
        clause.append(f'principalstate = {socrata.quote(state)}')
    return ' AND '.join(clause)


def count(segment, **kw):
    return socrata.count(DATASET, where=where(segment, **kw))


def pull(segment, limit=None, state='CO', good_standing=True):
    """Yield normalized prospect rows for one segment."""
    if segment not in SEGMENTS:
        raise KeyError(f'unknown CDOS segment {segment!r}')
    for r in socrata.fetch(DATASET, where=where(segment, state, good_standing), limit=limit):
        entity_id = (r.get('entityid') or '').strip()
        company = normalize.clean_entity_name(r.get('entityname') or '')
        if not company or not entity_id:
            continue

        first = (r.get('agentfirstname') or '').strip().title()
        last  = (r.get('agentlastname') or '').strip().title()
        if (r.get('agentorganizationname') or '').strip().lower() in _AGENT_SERVICES:
            first = last = ''

        address = (r.get('principaladdress1') or '').strip()
        city    = (r.get('principalcity') or '').strip().title()
        yield normalize.row(
            first_name=first, last_name=last, company=company,
            address=address, city=city,
            state=(r.get('principalstate') or '').strip(),
            zip=(r.get('principalzipcode') or '').strip()[:5],
            source_ref=f'cdos:{DATASET}:{entity_id}',
            icp_score=normalize.score(company=company, city=city, address=address,
                                      person=(first or last), active=good_standing),
        )
