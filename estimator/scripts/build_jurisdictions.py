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
    # Machine-checkable form of the same requirements, used by the Insurance
    # tab's scope-gap check: every item is searched for in the carrier's line
    # item descriptions, and anything absent is surfaced as a supplement
    # candidate with its code basis attached.
    #   class: 'code'        — code-mandated; strongest supplement argument
    #          'common'      — not code, but needed on essentially every reroof
    #          'conditional' — only applies if the roof has the feature, so it
    #                          is listed to check, never asserted as missing
    #   match: lowercase substrings tested against the carrier line description
    'code_items': [
        {'key': 'permit', 'class': 'code', 'label': 'Permit and inspection fees',
         'basis': 'Required by the AHJ for a full reroof',
         'match': ['permit', 'plan review'],
         'note': "Carriers often omit permit fees entirely — the AHJ's published fee schedule is the documentation."},
        {'key': 'drip_edge', 'class': 'code', 'label': 'Drip edge — eaves AND rakes',
         'basis': 'IRC R905.2.8.5',
         'match': ['drip edge', 'drip-edge', 'd/edge'],
         'note': 'The classic short-pay: eave footage paid, rake footage omitted. Check the LF against eave+rake, not eave alone.'},
        {'key': 'ice_water', 'class': 'code', 'label': 'Ice barrier (ice & water shield)',
         'basis': 'IRC R905.1.2 — eave to 24" inside the warm-wall line',
         'match': ['ice & water', 'ice and water', 'ice barrier', 'ice/water', 'i&w'],
         'note': 'Required throughout Colorado. Verify the LF covers valleys and the full 24" past the warm wall, not a token amount.'},
        {'key': 'underlayment', 'class': 'code', 'label': 'Underlayment (felt or synthetic)',
         'basis': 'IRC R905.1.1',
         'match': ['felt', 'underlayment', 'synthetic'],
         'note': 'Sometimes buried in the shingle line ("w/ felt") — confirm before supplementing.'},
        {'key': 'starter', 'class': 'code', 'label': 'Starter course',
         'basis': 'IRC R905.2.7 + manufacturer installation instructions',
         'match': ['starter'],
         'note': 'Manufacturer-required for the wind warranty; cut-shingle starter is not an approved substitute.'},
        {'key': 'ridge_cap', 'class': 'code', 'label': 'Hip / ridge cap shingles',
         'basis': 'Manufacturer installation instructions',
         'match': ['ridge cap', 'hip / ridge', 'hip and ridge', 'ridge / hip', 'ridge cap -'],
         'note': 'Must be a manufactured cap on a laminate system — check LF against hip+ridge.'},
        {'key': 'ventilation', 'class': 'code', 'label': 'Attic ventilation (1/300 net free area)',
         'basis': 'IRC R806.2',
         'match': ['ridge vent', 'roof vent', 'attic vent', 'turtle vent', 'box vent',
                   'static vent', 'power attic', 'turbine', 'off ridge', 'ventilat'],
         'note': 'Run the estimator’s vent calculator — if existing NFA is below code, the shortfall is a code-upgrade supplement.'},
        {'key': 'pipe_flashing', 'class': 'code', 'label': 'Pipe jack / vent flashings',
         'basis': 'IRC R905.2.8',
         'match': ['pipe jack', 'pipe flashing', 'flashing - pipe', 'lead jack', 'roof jack'],
         'note': 'Reusing old lead jacks is not code-compliant on a full replacement.'},
        {'key': 'debris_haul', 'class': 'common', 'label': 'Debris haul / dumpster',
         'basis': 'Not code — but every tear-off generates it',
         'match': ['dumpster', 'haul debris', 'debris', 'disposal', 'haul off'],
         'note': 'Sometimes folded into the tear-off line — confirm before supplementing.'},
        {'key': 'steep_charge', 'class': 'common', 'label': 'Steep-slope charge (7/12+)',
         'basis': 'Not code — pitch-driven labor',
         'match': ['steep'],
         'note': 'Pull the pitch off the measurement report; if any facet is 7/12 or greater this is owed.'},
        {'key': 'high_roof', 'class': 'common', 'label': 'High-roof charge (2 stories+)',
         'basis': 'Not code — access-driven labor',
         'match': ['high roof', '2 stories', 'two stor', 'two-stor'],
         'note': 'Owed on any roof two stories or greater regardless of pitch.'},
        {'key': 'detach_reset', 'class': 'common', 'label': 'Detach & reset (gutters, solar, satellite, A/C)',
         'basis': 'Not code — required to complete the tear-off',
         'match': ['detach & reset', 'detach and reset', 'detach &', 'r&r gutter', 'd&r'],
         'note': 'Anything mounted on or against the roof that has to come off and go back.'},
        {'key': 'overhead_profit', 'class': 'common', 'label': 'Overhead & profit',
         'basis': 'Not code — trade-complexity driven',
         'match': ['overhead', 'profit', 'o&p'],
         'note': 'Generally supportable when three or more trades are involved in the repair.'},
        {'key': 'valley_metal', 'class': 'conditional', 'label': 'Valley metal',
         'basis': 'IRC R905.2.8.2 (open valleys)',
         'match': ['valley metal', 'valley -', 'w valley', 'open valley'],
         'note': 'Only if the roof has open valleys — check the measurement report before claiming it.'},
        {'key': 'step_flashing', 'class': 'conditional', 'label': 'Step flashing (roof-to-wall)',
         'basis': 'IRC R905.2.8.3',
         'match': ['step flashing', 'flashing - step'],
         'note': 'Only where the roof meets a sidewall. New step flashing is required on a full replacement.'},
        {'key': 'chimney_flashing', 'class': 'conditional', 'label': 'Chimney / counter flashing',
         'basis': 'IRC R905.2.8.3',
         'match': ['counterflashing', 'counter flashing', 'chimney flash', 'apron flashing', 'chimney -'],
         'note': 'Only if the roof has a chimney or a masonry penetration.'},
    ],
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
