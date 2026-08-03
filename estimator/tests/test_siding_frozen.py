"""Safety net locking down the LP + Hardie siding pricing that already ships.

Anything that adds new siding measurements, bundles, products, or work-order
sections must not move the customer's number for an estimate built with today's
defaults. Two things get pinned:

  * the four 2026 QXO material costs — they are what every LP/Hardie sell
    price rolls off, and a fat-finger to any of the four moves every siding
    estimate in the field;
  * the calc_selected_total for a canonical b_lp_standard estimate at
    26 SQ / 10% waste / 35% margin — the reprice-identically check.

Written before any of the take-off changes touched the file, so a run that
was green before the change and red after tells us exactly what drifted.
"""
import pytest


# ── 1. The four QXO costs are load-bearing ─────────────────────────────────

_FROZEN_COSTS = {
    's_lp_standard':      163.61,   # LP SmartSide 8" Cedar Text Lap (field-painted)
    's_lp_expert':        245.14,   # LP SmartSide Expert Finish 8" Lap (pre-finished)
    's_hardie_primed':    201.29,   # James Hardie Primed 8.25" Cedar Mill Lap
    's_hardie_statement': 248.71,   # James Hardie Statement Collection 8.25" Lap
}


def test_qxo_bundle_costs_are_frozen(A):
    """These are the four costs every Good/Better/Best siding sell price rolls
    off. A fat-finger to any of them silently moves every estimate in the field
    — the drift will not show up in a lint or a parity test, only in the
    customer's number. Lock them at exact values."""
    seed = {p['id']: p for p in A.SIDING_CATALOG_SEED}
    for pid, expected_cost in _FROZEN_COSTS.items():
        assert pid in seed, f'{pid} disappeared from SIDING_CATALOG_SEED'
        assert seed[pid]['cost'] == expected_cost, (
            f'{pid} cost moved: {seed[pid]["cost"]} != {expected_cost}. '
            f'If this is intentional, update _FROZEN_COSTS with the new value AND '
            f'update the customer-facing G/B/B taglines that quote the finish.')


def test_qxo_bundle_tier_defaults_are_frozen(A):
    """Good/Better/Best defaults are the tier arrangement the field is trained on.
    Changing which bundle sits on which tier reorders every dashboard, every
    close-rate report, and every rep's expectation of what "the Better package"
    means. Lock them."""
    assert A.SIDING_TIER_DEFAULTS_SEED == {
        'good':   'b_lp_standard',
        'better': 'b_hardie_primed',
        'best':   'b_hardie_statement',
    }


# ── 2. A canonical b_lp_standard estimate must reprice identically ─────────
#
# The estimate below is what applyBundleToTier + applyMeasurements produces for
# a 26-SQ house on the Good tier at 10% waste + 35% margin — hand-built so this
# test stays independent of the loader while still catching any accidental
# pricing move. Only the two priced products in the b_lp_standard bundle
# contribute; every other bundle line is either $0 or zero-qty.


def _cell(cost):
    return {'material_unit_cost': cost, 'labor_unit_cost': 0,
            'description': '', 'notes': '', 'included': True}


def _lap_8_reference_estimate():
    """The four line items that carry cost on a 26-SQ LP standard estimate.
    Everything else in the bundle is $0 or zero-qty and drops out of the
    total — those rows do not need to be present for the pricing check."""
    def li(cid, name, unit, qty, cost):
        return {'id': cid, 'catalog_id': cid, 'name': name, 'unit': unit,
                'quantity': qty, 'measure': 'siding_sq_waste',
                'customer_visible': True,
                'tiers': {t: _cell(cost) for t in ('good', 'better', 'best')}}
    return {
        'estimate_type': 'retail', 'salesperson': 'luke', 'selected_tier': 'good',
        'measurements': {'siding_squares': 26, 'siding_waste_pct': 10},
        'customer': {'name': 'Test Reference House',
                     'address': {'street': '1 Elm', 'city': 'Loveland', 'state': 'CO'}},
        'pricing': {'mode': 'margin', 'global_rate': 35, 'tier_rates': {},
                    'trade_rates': {}, 'per_trade_overrides': {}},
        'trades': {'siding': {
            'enabled': True, 'mode': 'gbb', 'selected_tier': 'good',
            'tier_bundles': {'good': 'b_lp_standard', 'better': '', 'best': ''},
            'line_items': [
                li('s_lp_standard',    'LP SmartSide 8" Cedar Text Lap', 'SQ', 28.6, 163.61),
                li('sa_wrap_tribuilt', 'TriBuilt House Wrap',            'SQ', 28.6,   6.23),
            ],
        }},
    }


# What that estimate MUST price to today — anything that touches pricing math,
# the SQ→qty rounding, the margin resolver, or the two QXO costs moves it.
_LAP_8_SELL_TOTAL = (163.61 + 6.23) * 28.6 / (1 - 35 / 100)


def test_default_lap_8_estimate_reprices_identically(A):
    """A 26 SQ / 10% waste / 35% margin b_lp_standard estimate must price to the
    same number after every change in this branch. The two priced products are
    s_lp_standard ($163.61/SQ) and sa_wrap_tribuilt ($6.23/SQ), both auto-filled
    to 28.6 SQ. If this drifts, something moved a cost, a margin, a rounding
    rule, or the SQ→qty auto-fill — figure out which BEFORE bumping the value."""
    total = A.calc_selected_total(_lap_8_reference_estimate())
    assert total == pytest.approx(_LAP_8_SELL_TOTAL, rel=1e-9)
    # Sanity: the closed form comes out ~$7,472.96, so a drift of even a dollar
    # is a real signal — not a rounding artefact.
    assert total == pytest.approx(7472.9600, abs=0.01)
