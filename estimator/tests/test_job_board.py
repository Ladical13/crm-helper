"""The Job Board and the Analytics page: full-screen pages, not a modal.

Static checks against the front end, in the style of test_customer_file.py.
Each pins a rule that fails silently when broken — nothing errors, the page
just quietly goes back to doing the wrong thing.
"""
import os
import re

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
APP_JS = os.path.join(HERE, 'static', 'app.js')
INDEX = os.path.join(HERE, 'static', 'index.html')
STYLE = os.path.join(HERE, 'static', 'style.css')


def _read(p):
    with open(p, encoding='utf-8') as f:
        return f.read()


def _fn_body(src, name):
    i = src.index('function %s(' % name)
    return src[i:src.index('\n}', i) + 2]


def _css_rule(css, selector):
    """Every declaration block whose selector list names `selector`."""
    out = []
    for m in re.finditer(r'([^{}]+)\{([^{}]*)\}', css):
        sels = [s.strip() for s in m.group(1).split(',')]
        if selector in sels:
            out.append(m.group(2))
    return out


# ── pages, not a modal ────────────────────────────────────────────────

def test_the_dashboard_modal_is_gone():
    html = _read(INDEX)
    assert 'id="dashboard-modal"' not in html
    assert 'id="page-dashboard"' in html and 'id="page-analytics"' in html
    assert 'dash-modal-box' not in _read(STYLE)


def test_opening_the_dashboard_navigates():
    js = _read(APP_JS)
    assert "switchPage('dashboard')" in _fn_body(js, 'openDashboard')
    assert "switchPage('analytics')" in _fn_body(js, 'openAnalytics')
    body = _fn_body(js, 'switchPage')
    assert "page === 'dashboard'" in body and "page === 'analytics'" in body
    assert 'is-board' in body


def test_nothing_caps_the_width_of_either_page():
    css = _read(STYLE)
    for sel in ('.page-board', '.board-body', '.an-body', '.board-cols', '.an-grid'):
        for block in _css_rule(css, sel):
            assert 'max-width' not in block, f'{sel} has a max-width'


def test_both_pages_hide_the_estimate_chrome():
    css = _read(STYLE)
    for el in ('#sidebar', '#page-nav', '#print-pages-bar', '#save-btn'):
        assert f'body.is-board {el}' in css


def test_the_laptop_header_keeps_both_buttons():
    """At <=1599px the header hides every button but the estimate actions, and
    that is the width this gets used at. The Dashboard button used to vanish
    there, leaving it reachable only through the More menu."""
    css = _read(STYLE)
    i = css.index('@media (max-width: 1599px) {\n  .header-desktop-btns button:not(')
    assert ':not(.btn-dashboard)' in css[i:css.index('\n}', i)]
    html = _read(INDEX)
    assert 'onclick="openAnalytics()" class="btn-dashboard btn-analytics"' in html


# ── one column per job ────────────────────────────────────────────────

def test_every_estimate_lands_in_exactly_one_column():
    """The modal listed a sent estimate under both Outstanding and Sent, so the
    same dollars showed twice. boardColumnOf returns ONE key, and the columns
    are filled from it and nothing else."""
    js = _read(APP_JS)
    render = _fn_body(js, 'renderBoardColumns')
    assert 'boardColumnOf(e)' in render
    assert 'Outstanding' not in render
    col = _fn_body(js, 'boardColumnOf')
    # Going cold is decided by the one shared rule, not restated.
    assert 'estGoingCold(e)' in col
    assert 'estGoingCold(' in _fn_body(js, 'renderHomePage')


def test_the_post_signature_columns_are_served_not_mirrored():
    js = _read(APP_JS)
    assert "fetch('/api/job-stages')" in _fn_body(js, '_loadJobStages')
    assert "'scheduled'" not in _fn_body(js, 'boardColumns')


def test_marking_lost_from_the_board_asks_why():
    """The old status dropdown PATCHed 'lost' directly and skipped the reason
    picker, so the loss reasons on the analytics page under-counted every loss
    recorded from the dashboard."""
    js = _read(APP_JS)
    move = _fn_body(js, 'boardMove')
    assert 'openLostModal()' in move
    confirm = _fn_body(js, 'confirmLostReason')
    assert '_lostBoardId' in confirm
    assert "dashUpdateStatus(id, 'lost', reason, note)" in confirm


def test_a_signed_job_only_moves_between_job_stages():
    move = _fn_body(_read(APP_JS), 'boardMove')
    assert "target.startsWith('job:')" in move
    assert 'boardSetJobStage' in move


def test_the_card_still_opens_the_customer_file():
    card = _fn_body(_read(APP_JS), 'dashRow')
    assert 'custEstimateCount' in card
    assert "openCustomer('${jsq(e.customer_name)}')" in card
    # Delete carries a customer name into an inline handler too.
    assert "jsq(e.customer_name || 'this estimate')" in card


# ── touch ─────────────────────────────────────────────────────────────

def test_touch_rules_are_gated_on_the_pointer():
    """Drag-and-drop is unreliable on iOS, so the Move-to picker is the touch
    path. Its 16px (no focus-zoom) and 40px targets belong under a coarse
    pointer, never a width query — see the Mobile section of CLAUDE.md."""
    css = _read(STYLE)
    i = css.index('@media (pointer: coarse) {\n  .board-card[draggable="true"]')
    block = css[i:css.index('\n}', i)]
    assert '.board-move' in block and 'font-size: 16px' in block
    assert 'min-height: 40px' in block


def test_board_columns_contain_their_scroll():
    css = _read(STYLE)
    assert any('overscroll-behavior: contain' in b for b in _css_rule(css, '.board-col-body'))


# ── analytics ─────────────────────────────────────────────────────────

def test_analytics_asks_the_server_for_the_range_and_rep():
    js = _read(APP_JS)
    q = _fn_body(js, '_anQuery')
    for p in ("'from'", "'to'", "'rep'"):
        assert p in q
    assert "fetch('/api/analytics'" in _fn_body(js, 'loadAnalytics')


def test_the_kpis_are_the_servers_not_recomputed():
    """The KPI tiles were computed in the browser from /api/estimates, which
    can't honour a date range. They come from the server's `kpis` now."""
    page = _fn_body(_read(APP_JS), 'renderAnalyticsPage')
    assert 'ad.kpis' in page
    assert '_dashData' not in page


def test_a_rep_row_drills_in():
    page = _fn_body(_read(APP_JS), 'renderAnalyticsPage')
    assert "anSetRep('${jsq(name)}')" in page


def test_charts_are_drawn_at_their_real_width():
    js = _read(APP_JS)
    assert 'el.clientWidth' in _fn_body(js, '_drawCharts')
    assert 'requestAnimationFrame(_drawCharts)' in _fn_body(js, 'renderAnalyticsPage')
    # The monthly panel uses the SVG trend chart, not the old div bars.
    assert 'svgTrend(rows, W)' in _fn_body(js, 'renderMonthlyTrends')
