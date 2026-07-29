"""One row shape for every source.

The importer (`POST /crm/api/prospects/import`) reads exactly these keys and
ignores anything else, so a source is free to carry extra provenance. Two of
them are load-bearing:

  • `source_ref` — stable id of the originating record. It is what makes a
    re-import a no-op for rows that carry no contact details at all, which is
    most of the free open data.
  • `icp_score` — queue ordering only. Never a filter; a low score is still a
    real partner, just not the one to call first.
"""

# Front Range / Northern Colorado. A partner here is inside the service area
# rather than a five-hour drive away, which is most of what "fit" means for a
# roofer. Kept as data so the list can move without touching the scorer.
CORE_CITIES = {
    'fort collins', 'loveland', 'greeley', 'windsor', 'longmont', 'berthoud',
    'timnath', 'wellington', 'johnstown', 'milliken', 'severance', 'eaton',
    'evans', 'firestone', 'frederick', 'mead', 'erie', 'lafayette', 'louisville',
    'boulder', 'broomfield', 'brighton', 'thornton', 'westminster', 'arvada',
}

FIELDS = ['first_name', 'last_name', 'company', 'phone', 'email', 'address',
          'city', 'state', 'zip', 'website', 'license_no', 'source_ref']


def row(**kw):
    """A prospect row with every key present, blanks included."""
    out = {f: str(kw.get(f) or '').strip() for f in FIELDS}
    out['icp_score'] = int(kw.get('icp_score') or 0)
    return out


def score(company='', city='', address='', person='', active=True):
    """0-6, higher is called first. Deliberately simple and explainable.

    Reachability dominates: a record with a street address can be visited and a
    record with a named human can be asked for by name, whereas a bare company
    name in a distant town is a research task before it is a lead.
    """
    n = 0
    if address:
        n += 2                                    # can be called on or dropped in
    if person:
        n += 1                                    # ask for someone by name
    if (city or '').strip().lower() in CORE_CITIES:
        n += 2                                    # inside the service area
    if active:
        n += 1
    return n


def clean_entity_name(name):
    """Strip the status suffix Colorado appends to lapsed entity names.

    CDOS stores delinquency in the name itself — "ARG PROPERTY MANAGEMENT
    CORPORATION, Delinquent September 1, 2009" — so an unfiltered pull puts
    that whole string on a lead card.
    """
    s = (name or '').strip()
    for marker in (', Delinquent', ', Dissolved', ', Withdrawn', ', Administratively',
                   ', Voluntarily', ', Merged', ', Converted', ', Expired'):
        idx = s.find(marker)
        if idx > 0:
            s = s[:idx]
    return s.strip().rstrip(',').strip()
