"""Material vs labor — which side of the internal cost split a product lands on.

A catalog product carries ONE `cost`, and until `cost_class` existed every
seeding path dropped the whole of it into `material_unit_cost`. So the rep-only
Cost & Profit panel reported **Labor $0.00 on every estimate ever written**,
while l_install sat in the live book at $145/SQ being counted as material, and
the permit packet's Labor column printed $0 next to it.

Nothing about the money was wrong — material + labor was always the same number
— which is exactly why it survived so long. The split was the lie.

The rule this file exists to hold down: `cost_class` may only ever influence the
SPLIT. Never a total, a sell price, a customer-visible gate, a margin floor or a
quantity. If that ever stops being true, a manager reclassifying a product
retroactively changes what a customer was charged.
"""
import copy
import os
import re

import pytest

import app as A


LABOR_SEED_IDS = {
    'l_tearoff', 'l_install',
    'sl_tearoff', 'sl_install',
    'wl_removal', 'wl_install',
    'cl_tpo_to_mf', 'cl_tpo_to_fa', 'cl_epdm_to_mf', 'cl_epdm_to_fa',
    'cl_tpo_lo_mf', 'cl_tpo_lo_fa', 'cl_epdm_lo_mf', 'cl_epdm_lo_fa',
    'cl_coating', 'cl_labor_reroof', 'cl_labor_new',
}


def _seed_products():
    for trade, (catalog, _b, _t) in A.BUNDLE_SEEDS.items():
        for p in catalog:
            yield trade, p


# ── The seeds ─────────────────────────────────────────────────────────────

def test_every_seeded_labor_product_carries_a_labor_class():
    """These are the lines whose cost belongs in the Labor row. A seed product
    that loses its class silently rejoins Material and the panel under-reports
    labor on every job carrying it."""
    got = {p['id'] for _t, p in _seed_products() if p.get('cost_class') == 'labor'}
    assert got == LABOR_SEED_IDS


def test_no_seed_product_carries_a_class_that_is_not_one_of_the_two():
    """A third value would read as material everywhere and look deliberate."""
    stray = {p['id'] for _t, p in _seed_products()
             if p.get('cost_class') not in (None, 'material', 'labor')}
    assert not stray


def test_a_seeded_class_agrees_with_what_the_guess_would_say():
    """The seed literals and _guess_cost_class must not disagree: the guess is
    what reaches manager-created products, so a seed that contradicts it means
    two products with the same name get classed differently."""
    bad = [(p['id'], p['cost_class'], A._guess_cost_class(p['id'], p.get('name')))
           for _t, p in _seed_products() if 'cost_class' in p
           and p['cost_class'] != A._guess_cost_class(p['id'], p.get('name'))]
    assert not bad, f'seed disagrees with the guess: {bad}'


# ── The guess ─────────────────────────────────────────────────────────────

def test_a_product_with_no_class_reads_as_material():
    """Absence is the test, and absence means today's behaviour. An
    unclassified product must move no number anywhere."""
    assert A._norm_cost_class(None) == 'material'
    assert A._norm_cost_class('') == 'material'
    assert A._norm_cost_class('nonsense') == 'material'
    assert A._norm_cost_class('Labor') == 'labor'


def test_the_metal_delivery_line_is_not_labor():
    """x_ss_delivery is "Metal Delivery & Rollformer Set-Up" — a $368 supplier
    charge. A keyword match on "Set-Up" files it as crew time, which is why the
    exclusion list runs BEFORE the labor words rather than after."""
    assert A._guess_cost_class(
        'x_ss_delivery', 'Metal Delivery & Rollformer Set-Up') == 'material'


def test_a_screw_is_not_a_crew():
    """"crew" is the obvious word to add to the labor list, and a_ss_clips is
    "Seam Clips + Pancake ScREWs". It stays out on purpose."""
    assert A._guess_cost_class(
        'a_ss_clips', 'Seam Clips + Pancake Screws (1.5 in Mechanical)') == 'material'


@pytest.mark.parametrize('pid,name', [
    ('x_dumpster', 'Dumpster'),
    ('x_permit', 'Permit'),
    ('cx_freight', 'Material Freight / Delivery'),
    ('cl_moisture', 'Moisture Survey / Infrared Scan'),
    ('sx_rot', 'Rot Repair Allowance'),
])
def test_a_job_extra_is_material_not_labor(pid, name):
    """Job extras are deliberately material rather than a third bucket: the
    permit packet prints Cost Total = materials + labor, so a third bucket
    either drops out of that column or gets folded back in anyway."""
    assert A._guess_cost_class(pid, name) == 'material'


@pytest.mark.parametrize('pid,name', [
    ('p_ca99-4234-9e56-mtj90bd5', 'Standing Seam Install Labor'),
    ('p_a412-4819-afb1-mtj92ups', 'Exposed Fastener Labor'),
    ('p_d678-4a30-be6c-ms50c7jk', 'Install Labor LP'),
    ('p_5875-4712-9e5a-mt95m53i', 'Install Labor Hardie'),
    ('p_e301-42af-9675-mt95obar', 'Install Labor LP Painted'),
    ('p_061e-410c-8c90-mt95opvl', 'Install Labor Hardie Painted'),
])
def test_a_manager_created_product_is_classified_from_its_name(pid, name):
    """These six are real, live, and sold: they carry a p_<uid> id the seed has
    never heard of, so the id prefix cannot help and the NAME is the only
    signal. Between them they are the labor on every metal and painted-siding
    job this company writes."""
    assert A._guess_cost_class(pid, name) == 'labor'


def test_the_material_order_and_the_cost_class_agree_about_labor():
    """Two keyword lists that must not be merged — the order sheet answers "is
    this bought from a supplier" and correctly drops permits, which cost_class
    calls material. They have to agree about the word LABOR and nothing else,
    or a line is crew time on one screen and a purchase on another."""
    bad = [p['id'] for _t, p in _seed_products()
           if 'labor' in (p.get('name') or '').lower()
           and A._guess_cost_class(p['id'], p.get('name')) != 'labor']
    assert not bad, f'named labor but not classed labor: {bad}'


# ── Reaching a live book ──────────────────────────────────────────────────

def test_a_live_book_missing_the_class_gets_one_on_read():
    """_PRODUCT_BACKFILL_FIELDS only walks SEED ids, so a manager-created
    product would never get a class from it. Six of the labor products actually
    being sold on production are exactly that."""
    pb = A._ensure_bundle_catalogs({
        'roofing_catalog': [
            {'id': 'p_custom', 'name': 'Standing Seam Install Labor',
             'unit': 'SQ', 'cost': 300},
        ],
        'roofing_bundles': [],
    })
    got = next(p for p in pb['roofing_catalog'] if p['id'] == 'p_custom')
    assert got['cost_class'] == 'labor'


def test_a_seed_product_in_a_live_book_gets_its_class_backfilled():
    """A book saved before the field existed — which is every live book — has
    no cost_class on l_install either."""
    pb = A._ensure_bundle_catalogs({
        'roofing_catalog': [{'id': 'l_install', 'name': 'Install Labor',
                             'unit': 'SQ', 'cost': 145}],
        'roofing_bundles': [],
    })
    got = next(p for p in pb['roofing_catalog'] if p['id'] == 'l_install')
    assert got['cost_class'] == 'labor'
    assert got['cost'] == 145, "the backfill must never touch a manager's cost"


@pytest.mark.parametrize('chosen', ['material', 'labor'])
def test_a_manager_set_class_is_never_overwritten(chosen):
    """Absence is the test. An explicit 'material' on a product the guess calls
    labor is the manager CHOOSING, and it has to survive — otherwise a later
    improvement to the guess silently overrules every decision they made."""
    pb = A._ensure_bundle_catalogs({
        'roofing_catalog': [{'id': 'l_install', 'name': 'Install Labor',
                             'unit': 'SQ', 'cost': 145, 'cost_class': chosen}],
        'roofing_bundles': [],
    })
    got = next(p for p in pb['roofing_catalog'] if p['id'] == 'l_install')
    assert got['cost_class'] == chosen


def test_the_backfill_does_not_write_to_disk():
    """This is a read-time default, not a migration — the house rule is
    normalize on read, never rewrite stored records. _ensure_bundle_catalogs
    mutates the response only, so the caller's own dict stays untouched."""
    book = {'roofing_catalog': [{'id': 'l_install', 'name': 'Install Labor',
                                 'unit': 'SQ', 'cost': 145}],
            'roofing_bundles': []}
    before = copy.deepcopy(book)
    A._ensure_bundle_catalogs(copy.deepcopy(book))
    assert book == before


def test_every_live_product_ends_up_classified():
    """Whatever the guess decides, nothing may come back with the key missing —
    the read path reads cost_class and stops, so an absent key there is a
    product silently reverting to material."""
    pb = A._ensure_bundle_catalogs({})
    for key in (k for k in pb if k.endswith('_catalog')):
        if key == 'exterior_catalog':
            continue        # visual-only menu, never priced or split
        for p in pb[key]:
            if isinstance(p, dict) and p.get('id'):
                assert A._norm_cost_class(p.get('cost_class')) in ('material', 'labor')


# ── The Price Book control ────────────────────────────────────────────────

APP_JS = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                      'static', 'app.js')


def _app_js():
    return open(APP_JS, encoding='utf-8').read()


def test_the_price_book_offers_a_material_or_labor_control():
    """The guess ships to production as a default. Without somewhere to
    override it, a product it gets wrong is wrong forever."""
    src = _app_js()
    assert "pbRoofCatSet(${i},'cost_class',this.value)" in src
    assert 'pb-costclass-select' in src


def test_the_setter_coerces_to_the_two_allowed_values():
    """Anything but 'labor' has to read as material, or a typo in the DOM
    becomes a third state that every reader has to reason about."""
    assert "else if (field === 'cost_class') it.cost_class = normCostClass(val);" in _app_js()


def test_the_catalog_table_columns_and_colspans_agree():
    """The group subheader and the empty-state row span the whole table. Add a
    column and forget these and every grouped catalog renders misaligned —
    silently, and only for managers who grouped their products."""
    src = _app_js()
    i = src.index('function pbRenderRoofCatalog')
    block = src[i:src.index('\nfunction ', i + 10)]
    # `<th[ >]` and not a bare `<th` prefix — otherwise `<thead>` counts as a
    # column and the expected span is always one too many.
    n_cols = len(re.findall(r'<th[ >]', block))
    for span in ('<td colspan="%d">${esc(grp' % n_cols,
                 '<td colspan="%d" class="pb-empty"' % n_cols):
        assert span in block, f'expected a colspan of {n_cols}: {span}'


def test_the_control_has_a_style_rule():
    """style.css has no global utility classes — every class is scoped, so a
    class with no rule styles nothing and looks like it works."""
    css = open(os.path.join(os.path.dirname(APP_JS), 'style.css'), encoding='utf-8').read()
    assert '.pb-costclass-select' in css
    assert '.pb-th-costclass' in css
