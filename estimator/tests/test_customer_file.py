"""The Customer File — one customer, many estimates.

A homeowner is rarely one estimate. The roof goes on in spring, the siding is
quoted in autumn, the adjuster comes back and the whole thing is re-quoted.
The customer file is where those live together, and every test here guards a
way it quietly failed to do that.

None of these produced an error when they were broken:

  * The follow-on estimate carried no `crm_contact_id` or `crm_lead_id`, so it
    never joined the funnel to the lead the door-knock came from and
    `_push_to_den()` filed a SECOND Den contact for a customer The Den already
    had. Both halves of bid-versus-actual then looked fine and were wrong.
  * Grouping used a substring match on one side and an exact match on the
    other, so "Jon Smith" and "Jon Smithson" were one customer to the file and
    two to the button that creates the next estimate.
  * Duplicating an estimate renamed the customer to "Copy of Jon Smith", which
    moved the copy into a customer of its own — while duplicating is exactly
    how a rep builds a second estimate for someone.
  * Every button in the file passes the customer's name through an inline
    onclick. esc() escapes for HTML but not for the JS string literal, so
    O'Brien's buttons were a syntax error and did nothing at all.

A later pass moved estimate creation out of this modal entirely, into the
Documents door on the Customer hub — because the modal's "＋ Create New
Estimate" dropped a rep onto the exact same screen a brand-new customer sees
("Not saved yet", the typed label shown nowhere) with no sign this was
estimate #2 for someone who already had one. The Documents door now lists
every estimate for the loaded customer — including the one on screen right
now, even before its first save — and is where a new one gets started.
"""
import json
import os
import re
import shutil
import subprocess

import pytest

HERE   = os.path.dirname(os.path.abspath(__file__))
EST    = os.path.dirname(HERE)
APPJS  = os.path.join(EST, 'static', 'app.js')
RUNNER = os.path.join(HERE, 'customer_key_runner.js')


def _appjs():
    with open(APPJS, encoding='utf-8') as f:
        return f.read()


def _fn_body(src, name, code_only=False):
    """The source of `function <name>(` up to its closing brace at column 0.

    `code_only` drops // comment lines, so a test that forbids an old spelling
    is not tripped by the comment explaining why that spelling went away.
    """
    i = src.index('function %s(' % name)
    body = src[i:src.index('\n}', i) + 2]
    if code_only:
        body = '\n'.join(l for l in body.split('\n')
                         if not l.lstrip().startswith('//'))
    return body


# ── duplicating keeps the copy in the same customer's file ─────────────────

def test_duplicate_keeps_the_customer(client):
    eid = client.post('/api/estimates', json={
        'customer': {'name': 'Jon Smith', 'phone': '970-555-1212'},
    }).get_json()['estimate_id']

    new_id = client.post('/api/estimates/%s/duplicate' % eid).get_json()['estimate_id']
    copy = client.get('/api/estimates/%s' % new_id).get_json()

    assert copy['customer']['name'] == 'Jon Smith', \
        'the copy must stay in the same customer file — renaming it strands the duplicate'


def test_duplicate_marks_the_label_not_the_name(client):
    eid = client.post('/api/estimates', json={
        'customer': {'name': 'Jon Smith'},
        'estimate_label': 'Roof - Initial',
    }).get_json()['estimate_id']

    new_id = client.post('/api/estimates/%s/duplicate' % eid).get_json()['estimate_id']
    copy = client.get('/api/estimates/%s' % new_id).get_json()

    assert copy['estimate_label'] == 'Copy of Roof - Initial'


def test_duplicate_of_an_unlabelled_estimate_still_says_copy(client):
    """The label is the only thing telling two of one customer's estimates
    apart, so a copy must never come back indistinguishable from its source."""
    eid = client.post('/api/estimates', json={
        'customer': {'name': 'Jon Smith'},
    }).get_json()['estimate_id']

    new_id = client.post('/api/estimates/%s/duplicate' % eid).get_json()['estimate_id']
    assert client.get('/api/estimates/%s' % new_id).get_json()['estimate_label'] == 'Copy'


def test_duplicating_a_copy_does_not_stack_the_prefix(client):
    eid = client.post('/api/estimates', json={
        'customer': {'name': 'Jon Smith'},
        'estimate_label': 'Copy of Roof - Initial',
    }).get_json()['estimate_id']

    new_id = client.post('/api/estimates/%s/duplicate' % eid).get_json()['estimate_id']
    assert client.get('/api/estimates/%s' % new_id).get_json()['estimate_label'] \
        == 'Copy of Roof - Initial'


def test_both_estimates_reach_the_list_under_one_customer(client):
    """The file is built from /api/estimates, so the label and the name have to
    survive into the list projection or the file renders two blank rows."""
    eid = client.post('/api/estimates', json={
        'customer': {'name': 'Jon Smith'}, 'estimate_label': 'Roof',
    }).get_json()['estimate_id']
    new_id = client.post('/api/estimates/%s/duplicate' % eid).get_json()['estimate_id']

    rows = {e['estimate_id']: e for e in client.get('/api/estimates').get_json()}
    assert rows[eid]['customer_name'] == rows[new_id]['customer_name'] == 'Jon Smith'
    assert rows[eid]['estimate_label'] == 'Roof'
    assert rows[new_id]['estimate_label'] == 'Copy of Roof'


# ── customer notes land on the same customer the browser grouped ───────────

@pytest.mark.parametrize('written,read_back', [
    ('Jon Smith',   'jon smith'),
    ('Jon Smith',   '  Jon   Smith  '),
    ('Jon  Smith',  'Jon Smith'),
])
def test_notes_follow_the_same_key_the_file_groups_on(client, written, read_back):
    client.put('/api/customer-notes/%s' % written, json={'notes': 'HOA contact is Dana'})
    got = client.get('/api/customer-notes/%s' % read_back).get_json()
    assert got['notes'] == 'HOA contact is Dana'


def test_notes_do_not_leak_between_similar_names(client):
    """The counterpart to the substring bug: Smithson is a different person and
    must not read Smith's notes."""
    client.put('/api/customer-notes/Jon Smith', json={'notes': 'budget is tight'})
    assert client.get('/api/customer-notes/Jon Smithson').get_json()['notes'] == ''


def test_the_python_key_matches_the_javascript_one(A):
    assert A._cust_key('  Jon   SMITH ') == 'jon smith'
    assert A._cust_key(None) == ''


# ── the follow-on estimate keeps every link to the rest of the funnel ──────

LINK_FIELDS = ('crm_contact_id', 'crm_project_id', 'crm_job_number', 'crm_lead_id')


def test_the_link_fields_are_declared_together():
    src = _appjs()
    m = re.search(r'const CUSTOMER_LINK_FIELDS = \[(.*?)\];', src, re.S)
    assert m, 'CUSTOMER_LINK_FIELDS must stay a named list, not inline strings'
    declared = set(re.findall(r"'([a-z_]+)'", m.group(1)))
    assert declared == set(LINK_FIELDS), declared


def test_the_follow_on_estimate_copies_every_link_field():
    """Without these the second estimate for a customer is an orphan: invisible
    to the funnel's lead, and a duplicate contact in The Den at signature."""
    body = _fn_body(_appjs(), 'newEstimateForCustomer')
    assert 'CUSTOMER_LINK_FIELDS' in body, \
        'newEstimateForCustomer must carry the CRM links onto the new estimate'
    assert re.search(r'if \(!S\.customer\[k\] && c\[k\]\)', body), \
        'links must be copied only when blank, so a live CRM handoff still wins'


def test_a_link_field_that_exists_on_the_estimate_is_not_forgotten():
    """Every field in CUSTOMER_LINK_FIELDS has to be a real slot on the blank
    estimate, or it is copied into a key nothing reads."""
    src = _appjs()
    i = src.index('customer: { crm_contact_id')
    blank = src[i:src.index('address:', i)]
    for field in LINK_FIELDS:
        assert field in blank, '%s is not on the blank estimate' % field


# ── one grouping key, used on both sides ───────────────────────────────────

def test_the_customer_file_groups_on_the_shared_key():
    body = _fn_body(_appjs(), 'openCustomerFile', code_only=True)
    assert 'custKey' in body
    assert '.includes(' not in body, \
        "substring grouping put every Smithson into Jon Smith's file"


def test_the_create_button_groups_on_the_shared_key():
    body = _fn_body(_appjs(), 'newEstimateForCustomer')
    assert body.count('custKey') >= 2, \
        'the pre-fill lookup must use the same key the file was opened with'


def test_the_commercial_type_is_offered_when_creating_the_next_estimate():
    """Commercial reached the sidebar and never reached this dialog, so the
    only way to make a commercial estimate for an existing customer was to
    start from scratch and retype their details. The dialog lives on the
    Documents door now, not the modal — this must keep holding there."""
    src = _appjs()
    dialog = _fn_body(src, 'renderDocumentsPage', code_only=True)
    for t in ('retail', 'insurance', 'commercial'):
        assert "docSetType('%s')" % t in dialog, t
    assert 'ESTIMATE_TYPES' in _fn_body(src, 'docSetType'), \
        'drive the active state off ESTIMATE_TYPES so a fourth type cannot be missed here'


# ── creation moved into the Documents door ──────────────────────────────

def test_the_create_form_is_gone_from_the_customer_file_modal():
    """The modal's job narrowed to finding a customer, not creating for one —
    a leftover copy of the form here would let the two drift out of sync."""
    src = _appjs()
    assert 'function cfToggleCreate' not in src
    assert 'function cfSetType' not in src
    assert 'function cfCreateEstimate' not in src
    body = _fn_body(src, 'renderCustomerFile', code_only=True)
    assert 'cf-create-body' not in body and 'cf-label-input' not in body


def test_the_create_form_lives_on_the_documents_door():
    src = _appjs()
    assert 'function docToggleCreate' in src
    assert 'function docSetType' in src
    assert 'function docCreateEstimate' in src
    body = _fn_body(src, 'renderDocumentsPage', code_only=True)
    assert 'doc-create-body' in body and 'doc-label-input' in body


def test_the_modal_create_button_hands_off_to_documents():
    """Clicking "＋ New Estimate for X" in the modal must land the rep on
    Documents with the right customer's context loaded, not just close and
    leave them wherever they were."""
    body = _fn_body(_appjs(), 'cfGotoNewEstimate')
    assert 'doLoadEstimate' in body
    assert "switchPage('documents')" in body
    assert '_docCreatePending = true' in body


def test_documents_opens_the_create_form_when_handed_off_from_the_modal():
    body = _fn_body(_appjs(), 'renderDocumentsPage', code_only=True)
    assert '_docCreatePending' in body and 'docToggleCreate(true)' in body


def test_the_new_estimate_button_is_not_offered_with_zero_estimates():
    """The modal's 📁 button appears as soon as a customer name is typed —
    including while a rep is mid-creating that customer's very first, unsaved
    estimate, when the match list is empty. There is no "most recent
    estimate" to hand off to in that case, so the button must not render
    (estimates[0] would be undefined) — the rep is already on the only
    estimate this customer has."""
    body = _fn_body(_appjs(), 'renderCustomerFile', code_only=True)
    m = re.search(r'\$\{estimates\.length \? `(.*?)` : `(.*?)`\}', body, re.S)
    assert m, 'the create-button section must branch on estimates.length'
    has_estimates, zero_estimates = m.group(1), m.group(2)
    assert 'cfGotoNewEstimate' in has_estimates and 'estimates[0]' in has_estimates
    assert 'cfGotoNewEstimate' not in zero_estimates, \
        'zero estimates means nothing to hand off to — the button must not appear'


# ── the Documents door lists every estimate, including the unsaved one ─────

def test_the_documents_door_groups_on_the_shared_key():
    body = _fn_body(_appjs(), 'docEstimateListHtml', code_only=True)
    assert 'custKey' in body
    assert '.includes(' not in body, \
        "substring grouping put every Smithson into Jon Smith's estimate list"


def test_the_current_row_is_not_clickable():
    """Reloading the estimate you're already looking at is a wasted request
    and, worse, a confusing no-op click."""
    body = _fn_body(_appjs(), 'estRowHtml')
    assert re.search(r"current \? '' :", body), \
        'the current row must not carry a doLoadEstimate click handler'


def test_an_unsaved_estimate_is_never_filtered_out_of_its_own_list():
    """The literal bug reported: create estimate #2 for a customer and, before
    the first autosave, it must already show up here under its typed label —
    not look indistinguishable from starting a whole new customer."""
    body = _fn_body(_appjs(), 'docEstimateListHtml', code_only=True)
    assert 'estimate_id: S.estimate_id || null' in body, \
        'the current row must be built even when S.estimate_id is still null'
    assert re.search(r'\[estRowHtml\(currentRow', body), \
        'the current (possibly unsaved) estimate must always lead the list'


def test_every_estimate_type_gets_a_visible_icon_even_with_a_custom_label():
    """A custom label used to swallow the type entirely — an Insurance
    estimate with a label like "Storm Claim" looked identical to a Retail
    one. The icon must render regardless of whether a label is set."""
    body = _fn_body(_appjs(), 'estRowHtml')
    assert 'EST_TYPE_ICON[e.estimate_type]' in body
    assert 'cf-est-label' in body and 'icon' in body


# ── the customer file is reachable from the lists reps actually read ───────

def test_the_dashboard_row_links_to_the_customer_file():
    """The file used to be reachable only from a home-screen search box and a
    sidebar button that appears once a name is typed, so a rep reading a list
    of estimates had no way to see that three of them are one customer."""
    body = _fn_body(_appjs(), 'dashRow')
    assert 'custEstimateCount' in body and 'openCustomerFile' in body


def test_the_home_screen_rows_link_to_the_customer_file():
    src = _appjs()
    i = src.index('home-recents-hd')
    home = src[i:src.index('home-empty', i)]
    assert 'custEstimateCount' in home and 'openCustomerFile' in home


def test_the_count_is_rebuilt_wherever_the_list_is_replaced():
    """A stale count is worse than none — it offers a file link for a customer
    whose other estimates the rep can no longer see."""
    src = _appjs()
    assignments = re.findall(r'_dashData\s*=\s*(?:await\s+r\.json\(\)|_dashData\.filter)', src)
    assert len(assignments) >= 4
    assert src.count('rebuildCustCounts()') >= len(assignments), \
        'every _dashData replacement must be followed by rebuildCustCounts()'


# ── an apostrophe must not kill the buttons ────────────────────────────────

def test_every_handler_carrying_a_customer_name_quotes_it_for_javascript():
    src = _appjs()
    bare = re.findall(r"on\w+=\"[^\"]*\(\s*'\$\{esc\((?:name|r\.name|e\.customer_name)\)\}'", src)
    assert not bare, \
        'esc() does not escape the apostrophe that closes the JS argument: %r' % bare


@pytest.fixture(scope='module')
def js(tmp_path_factory):
    if shutil.which('node') is None:
        pytest.skip('node not installed')
    d = tmp_path_factory.mktemp('custkey')
    fx, out = d / 'fixtures.json', d / 'js.json'
    fx.write_text(json.dumps({
        'keys':   ['  Jon   SMITH ', 'Jon Smith', 'Jon Smithson', '', None],
        'quotes': ["O'Brien", 'Plain Name', 'back\\slash', 'quote"mark'],
    }), encoding='utf-8')
    proc = subprocess.run(['node', RUNNER, str(fx), str(out)],
                          capture_output=True, text=True)
    assert proc.returncode == 0, 'customer_key_runner.js failed:\n%s' % proc.stderr
    return json.loads(out.read_text(encoding='utf-8'))


def test_custkey_collapses_case_and_whitespace(js):
    keys = js['keys']
    assert keys[0] == keys[1] == 'jon smith'
    assert keys[2] == 'jon smithson', 'Smithson is a different customer'
    assert keys[3] == '' and keys[4] == '', 'a missing name is never a customer'


def test_jsq_escapes_the_apostrophe_and_the_backslash(js):
    obrien, plain, slash, quote = js['quotes']
    assert obrien == "O\\'Brien"
    assert plain == 'Plain Name', 'an ordinary name must pass through untouched'
    assert slash == 'back\\\\slash', 'a lone backslash would escape the closing quote'
    assert quote == 'quote&quot;mark', 'still HTML-escaped for the attribute'
