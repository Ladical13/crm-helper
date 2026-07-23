#!/usr/bin/env python3
"""Generate estimator/jurisdictions.json — the statewide Colorado permit /
jurisdiction reference used by the Scope-page "Permit Jurisdiction & Code" panel.

The municipality -> county mapping is parsed *deterministically* from the raw
wikitext of Wikipedia's "List of municipalities in Colorado" (vendored beside
this script as co_munis.wikitext). We parse with code, never an LLM, because a
roofing rep will act on this data — a fabricated town or wrong county is worse
than a missing one.

Re-run:  python build_jurisdictions.py            # uses vendored wikitext
         python build_jurisdictions.py --fetch    # refresh wikitext first
Output:  ../jurisdictions.json  (relative to this script)
"""
import re, json, os, sys, datetime

HERE = os.path.dirname(os.path.abspath(__file__))
WIKITEXT = os.path.join(HERE, 'co_munis.wikitext')
OUT = os.path.normpath(os.path.join(HERE, '..', 'jurisdictions.json'))
WIKI_URL = ('https://en.wikipedia.org/w/index.php?'
            'title=List_of_municipalities_in_Colorado&action=raw')

# The 64 counties of Colorado (Dept. of Local Affairs). Every CO parcel is in
# exactly one; these are the "unincorporated / county AHJ" catch-alls.
COUNTIES = [
    'Adams', 'Alamosa', 'Arapahoe', 'Archuleta', 'Baca', 'Bent', 'Boulder',
    'Broomfield', 'Chaffee', 'Cheyenne', 'Clear Creek', 'Conejos', 'Costilla',
    'Crowley', 'Custer', 'Delta', 'Denver', 'Dolores', 'Douglas', 'Eagle',
    'El Paso', 'Elbert', 'Fremont', 'Garfield', 'Gilpin', 'Grand', 'Gunnison',
    'Hinsdale', 'Huerfano', 'Jackson', 'Jefferson', 'Kiowa', 'Kit Carson',
    'La Plata', 'Lake', 'Larimer', 'Las Animas', 'Lincoln', 'Logan', 'Mesa',
    'Mineral', 'Moffat', 'Montezuma', 'Montrose', 'Morgan', 'Otero', 'Ouray',
    'Park', 'Phillips', 'Pitkin', 'Prowers', 'Pueblo', 'Rio Blanco',
    'Rio Grande', 'Routt', 'Saguache', 'San Juan', 'San Miguel', 'Sedgwick',
    'Summit', 'Teller', 'Washington', 'Weld', 'Yuma',
]
assert len(COUNTIES) == 64, len(COUNTIES)

# ── Colorado code baseline — inherited by every jurisdiction unless overridden.
# Colorado has no statewide residential building code; each city/county adopts
# its own. These are the reroof-relevant items common across CO adoptions of
# the IRC. The verify_note is ALWAYS shown by the UI — this is a starting point
# for the rep, never a legal authority.
COLORADO_BASELINE = {
    'code_points': [
        "Colorado has no statewide residential building code — the local city or county adopts and enforces its own. Confirm the adopted IRC year and any local amendments with this jurisdiction.",
        "Ice barrier (self-adhering underlayment) typically required from the eave to at least 24\" inside the exterior warm-wall line.",
        "Balanced attic ventilation to code — 1 sq ft net free area per 300 sq ft of attic (1/300 rule).",
        "Drip edge required at eaves and rakes.",
        "Class 4 impact-resistant shingles strongly recommended for CO hail (insurance-driven, not usually code-mandated).",
        "Most CO jurisdictions require a permit and a final inspection for a full reroof.",
    ],
    'verify_note': "Colorado is home-rule for building codes — always confirm the adopted code year, local amendments, and the reroof submittal method with this jurisdiction before pulling the permit.",
}

# ── Curated overrides (merged over the generated entry, keyed by id). Kept
# deliberately small and factual: only details we can stand behind. Everything
# else inherits the baseline + the municipality's own website (auto-parsed).
CURATED = {
    'loveland': {
        'permit_template': 'loveland',
        'office': 'City of Loveland Building Division',
        'pull': "Reroof permit + roofing affidavit. Generate the packet in the estimator's Documents tab, then e-mail it to eplan-buildingfasttrack@cityofloveland.org (Fast Track). An online portal is also available.",
        'match': {'cities': ['Loveland'], 'zips': ['80537', '80538', '80539']},
        'code_points': [
            "Roofing affidavit required with the reroof permit (roof covering type/class, fasteners, underlayment, sheathing).",
            "Class 4 (impact-resistant) roof covering is the shop standard here.",
        ],
    },
}


def slug(s):
    s = re.sub(r"[^a-z0-9]+", '-', s.lower()).strip('-')
    return s


def link_text(s):
    m = re.search(r'\[\[([^\]]+?)\]\]', s)
    return m.group(1).split('|')[-1].strip() if m else None


def county_names(cell):
    """All CO counties referenced in a county cell, normalized + de-duped."""
    out = []
    for m in re.finditer(r'\[\[([^\]]*County, Colorado[^\]]*?)\]\]', cell):
        disp = m.group(1).split('|')[-1].strip()
        disp = re.sub(r'\s+count(y|ies)$', '', disp, flags=re.I).strip()
        if disp and disp not in out:
            out.append(disp)
    return out


def parse_munis(txt):
    lines = txt.splitlines()
    start = next(i for i, l in enumerate(lines)
                 if 'active municipalities of the State of Colorado' in l)
    end = next(i for i in range(start, len(lines)) if lines[i].strip() == '|}')
    region = lines[start:end]

    rows, cur, cells = [], None, []

    def flush():
        nonlocal cur, cells
        if cur is None:
            return
        typ = link_text(cells[0]) if cells else ''
        county_cell = cells[1] if len(cells) > 1 else ''
        web = next((c for c in cells
                    if re.search(r'\[https?://', c) and ' of ' in c), '')
        rows.append({'name': cur, 'type': typ or '',
                     'counties': county_names(county_cell), 'web': web})
        cur, cells = None, []

    for l in region:
        if l.startswith('!scope=row'):
            flush(); cur = link_text(l)
        elif l.startswith('|-'):
            flush()
        elif l.startswith('|') and cur is not None:
            cells.append(l)
    flush()
    return rows


def main():
    if '--fetch' in sys.argv:
        import urllib.request
        req = urllib.request.Request(WIKI_URL, headers={'User-Agent': 'p1-estimator-seed/1.0'})
        data = urllib.request.urlopen(req, timeout=30).read().decode('utf-8')
        open(WIKITEXT, 'w', encoding='utf-8').write(data)

    txt = open(WIKITEXT, encoding='utf-8').read()
    rows = parse_munis(txt)
    if len(rows) != 273:
        raise SystemExit(f'Expected 273 municipalities, parsed {len(rows)} — table format changed.')

    jurisdictions = []

    # County catch-alls (all 64).
    for c in COUNTIES:
        jid = slug(c + ' county')
        jurisdictions.append({
            'id': jid,
            'name': f'{c} County',
            'kind': 'county',
            'county': c,
            'counties': [c],
            'office': f'{c} County Building Department',
            'pull': '',
            'phone': '',
            'url': '',
            'permit_template': None,
            'code_points': [],
        })

    # Municipalities (273).
    seen = {j['id'] for j in jurisdictions}
    for r in rows:
        name = r['name']
        gov = 'town' if 'town' in r['type'].lower() else 'city'
        counties = r['counties']
        url, label = '', ''
        mweb = re.search(r'\[(https?://[^\s\]]+)\s+([^\]]+)\]', r['web'])
        if mweb:
            url, label = mweb.group(1).strip(), mweb.group(2).strip()
        if not counties:                    # Denver / Broomfield (consolidated)
            counties = [name]
        office = label or (('City of ' if gov == 'city' else 'Town of ') + name)
        jid = slug(name)
        n = 2
        while jid in seen:
            jid = slug(name) + f'-{n}'; n += 1
        seen.add(jid)
        jurisdictions.append({
            'id': jid,
            'name': office,
            'kind': 'city',
            'gov': gov,
            'county': counties[0],
            'counties': counties,
            'office': office,
            'pull': '',
            'phone': '',
            'url': url,
            'permit_template': None,
            'code_points': [],
            'match': {'cities': [name], 'zips': []},
        })

    # Apply curated overrides.
    by_id = {j['id']: j for j in jurisdictions}
    for jid, patch in CURATED.items():
        if jid not in by_id:
            raise SystemExit(f'CURATED id not found: {jid}')
        tgt = by_id[jid]
        for k, v in patch.items():
            if k == 'match':
                tgt.setdefault('match', {}).update(v)
            else:
                tgt[k] = v

    doc = {
        'version': 1,
        'generated_by': 'scripts/build_jurisdictions.py',
        'generated_on': datetime.date.today().isoformat(),
        'source': 'Wikipedia: List of municipalities in Colorado (raw wikitext)',
        'colorado_baseline': COLORADO_BASELINE,
        'jurisdictions': jurisdictions,
    }
    json.dump(doc, open(OUT, 'w', encoding='utf-8'), indent=2, ensure_ascii=False)
    n_city = sum(1 for j in jurisdictions if j['kind'] == 'city')
    n_cty = sum(1 for j in jurisdictions if j['kind'] == 'county')
    print(f'Wrote {OUT}: {n_cty} counties + {n_city} municipalities = {len(jurisdictions)} jurisdictions')


if __name__ == '__main__':
    main()
