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

Two later passes changed where all this lives. Creation moved off the modal,
because "＋ Create New Estimate" dropped a rep onto the same screen a
brand-new customer sees — "Not saved yet", the typed label shown nowhere, no
sign this was estimate #2 for someone who already had one. Then the modal
went entirely: it and the Documents page were two views of one question
("what does this customer have?") that could and did disagree.

There is now ONE customer screen — details, notes, every estimate including
the unsaved one on screen, and the files — and the estimate tab strip behind
a single button on it.
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
INDEX  = os.path.join(EST, 'static', 'index.html')
RUNNER = os.path.join(HERE, 'customer_key_runner.js')


def _appjs():
    with open(APPJS, encoding='utf-8') as f:
        return f.read()


def _read_index():
    with open(INDEX, encoding='utf-8') as f:
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

def test_opening_a_customer_groups_on_the_shared_key():
    body = _fn_body(_appjs(), 'openCustomer', code_only=True)
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
    dialog = _fn_body(src, 'renderClientPage', code_only=True)
    for t in ('retail', 'insurance', 'commercial'):
        assert "docSetType('%s')" % t in dialog, t
    assert 'ESTIMATE_TYPES' in _fn_body(src, 'docSetType'), \
        'drive the active state off ESTIMATE_TYPES so a fourth type cannot be missed here'


# ── one customer screen ─────────────────────────────────────────────────

def test_the_customer_file_modal_is_gone():
    """A customer is a place you go, not a thing you peek at over the top of
    what you were doing. The modal duplicated the estimate list that lives on
    the customer screen, so the two could disagree — and did."""
    src, html = _appjs(), _read_index()
    for dead in ('function renderCustomerFile', 'function openCustomerFile',
                 'function closeCustomerFile', 'function cfGotoNewEstimate',
                 'function loadCustomerAttachments', '_docCreatePending'):
        assert dead not in src, '%s survived the modal removal' % dead
    assert 'customer-file-modal' not in html
    assert 'id="page-documents"' not in html,         'Documents is part of the customer screen now, not a page of its own'


def test_the_customer_screen_holds_everything_about_the_customer():
    """Details, notes, every estimate, and the files — one page, one scroll."""
    body = _fn_body(_appjs(), 'renderClientPage', code_only=True)
    assert 'docEstimateListHtml()' in body, 'their estimates'
    assert 'cf-notes-ta' in body and 'saveCustomerNotes' in body, 'their notes'
    assert 'doc-create-body' in body and 'doc-label-input' in body, 'create form'
    assert 'renderDocumentsPage()' in body, 'their files and documents'


def test_documents_content_lives_inside_the_customer_page():
    """The container moved rather than being rebuilt, so the 11 in-page
    refresh callers of renderDocumentsPage() keep working untouched."""
    html = _read_index()
    m = re.search(r'<div id="page-client".*?</div>\s*</div>', html, re.S)
    assert m, '#page-client not found'
    assert 'id="documents-content"' in m.group(0)


def test_documents_still_resolves_as_a_page_name():
    """Deep links, the header badge and muscle memory all still say
    'documents'; switchPage aliases rather than breaking them."""
    body = _fn_body(_appjs(), 'switchPage', code_only=True)
    assert re.search(r"if \(page === 'documents'\) page = 'client'", body)


def test_the_create_form_moved_with_it():
    src = _appjs()
    for fn in ('docToggleCreate', 'docSetType', 'docCreateEstimate'):
        assert 'function %s' % fn in src, fn
    # and is gone from the page it used to live on
    assert 'doc-create-body' not in _fn_body(src, 'renderDocumentsPage', code_only=True)


def test_opening_a_customer_does_not_silently_bin_unsaved_work():
    """Every other route into a different estimate is a control the rep chose
    deliberately. A 📁 badge on a dashboard row does not read like "discard my
    draft", so an unsaved new estimate asks first."""
    body = _fn_body(_appjs(), 'openCustomer', code_only=True)
    assert 'confirm(' in body
    assert re.search(r"custKey\(\(S\.customer \|\| \{\}\)\.name\) === custKey\(name\)", body),         'already inside this customer? navigate, never reload'


# ── the Documents door lists every estimate, including the unsaved one ─────

def test_the_estimate_list_groups_on_the_shared_key():
    body = _fn_body(_appjs(), 'customerEstimateRows', code_only=True)
    assert 'custKey' in body
    assert '.includes(' not in body, \
        "substring grouping put every Smithson into Jon Smith's estimate list"


def test_one_list_answers_what_this_customer_has():
    """The Documents door and the Customer File modal used to answer the same
    question — "what does this customer have?" — and answered it with two
    separate implementations. Only one of them knew about the estimate being
    worked on, so opening the modal mid-estimate reported the customer had
    none. Both go through customerEstimateRows() now."""
    src = _appjs()
    assert 'function customerEstimateRows' in src
    assert 'customerEstimateRows(' in _fn_body(src, 'docEstimateListHtml', code_only=True)
    assert 'docEstimateListHtml()' in _fn_body(src, 'renderClientPage', code_only=True)


def test_the_open_estimate_is_not_stamped_into_a_stranger_s_file():
    """The modal opens for any customer — a dashboard row, the home search —
    not just the loaded one. The current estimate may only be spliced into
    the file of the customer it actually belongs to."""
    body = _fn_body(_appjs(), 'customerEstimateRows', code_only=True)
    assert 'isLoaded' in body
    assert re.search(r'return isLoaded \?', body), \
        'the current row must be conditional on it being this customer'


def test_the_current_row_is_not_clickable():
    """Reloading the estimate you're already looking at is a wasted request
    and, worse, a confusing no-op click."""
    body = _fn_body(_appjs(), 'estRowHtml')
    assert re.search(r"current \? '' :", body), \
        'the current row must not carry a doLoadEstimate click handler'


def test_an_unsaved_estimate_is_never_filtered_out_of_its_own_list():
    """Create estimate #2 for a customer and, before the first autosave, it
    must already show up under the label just typed — not look
    indistinguishable from starting a whole new customer. It has no id and is
    not in /api/estimates at all, so it can only come from S."""
    src = _appjs()
    row = _fn_body(src, 'currentEstimateRow', code_only=True)
    assert 'estimate_id:     S.estimate_id || null' in row, \
        'the current row must be built even when S.estimate_id is still null'
    body = _fn_body(src, 'customerEstimateRows', code_only=True)
    assert 'currentEstimateRow()' in body and 'current: true' in body, \
        'the current (possibly unsaved) estimate must lead the list'


def test_the_saved_copy_of_the_open_estimate_is_not_listed_twice():
    """Once saved, the open estimate is in BOTH S and the fetched list."""
    body = _fn_body(_appjs(), 'customerEstimateRows', code_only=True)
    assert re.search(r'!\(isLoaded && e\.estimate_id === S\.estimate_id\)', body), \
        'the fetched copy of the open estimate must be filtered out of `others`'


# ── renaming an estimate after the fact ────────────────────────────────────

def test_the_label_endpoint_renames_without_touching_anything_else(client):
    """A rep types "Roof" on the doorstep and wants "Roof - Insurance
    Supplement" once the adjuster has been. The rename must be a narrow
    patch, never a full-doc save that could push stale in-memory state over
    newer server state."""
    eid = client.post('/api/estimates', json={
        'customer': {'name': 'Jon Smith', 'phone': '970-555-1212'},
        'estimate_label': 'Roof',
    }).get_json()['estimate_id']

    r = client.patch('/api/estimates/%s/label' % eid,
                     json={'label': 'Roof - Insurance Supplement'})
    assert r.status_code == 200

    back = client.get('/api/estimates/%s' % eid).get_json()
    assert back['estimate_label'] == 'Roof - Insurance Supplement'
    assert back['customer']['phone'] == '970-555-1212', \
        'a rename must not disturb the rest of the estimate'


def test_a_renamed_estimate_shows_its_new_name_in_the_list(client):
    """The Documents door builds its list from /api/estimates, so a rename
    that never reaches the list projection is a rename the rep cannot see."""
    eid = client.post('/api/estimates', json={
        'customer': {'name': 'Jon Smith'}, 'estimate_label': 'Roof',
    }).get_json()['estimate_id']
    client.patch('/api/estimates/%s/label' % eid, json={'label': 'Siding'})

    row = next(e for e in client.get('/api/estimates').get_json()
               if e['estimate_id'] == eid)
    assert row['estimate_label'] == 'Siding'


def test_clearing_the_name_is_allowed(client):
    """An empty label falls back to the type name in the UI, which is what the
    row read before it was ever named — so blanking it must not 400."""
    eid = client.post('/api/estimates', json={
        'customer': {'name': 'Jon Smith'}, 'estimate_label': 'Roof',
    }).get_json()['estimate_id']
    assert client.patch('/api/estimates/%s/label' % eid,
                        json={'label': ''}).status_code == 200
    assert client.get('/api/estimates/%s' % eid).get_json()['estimate_label'] == ''


def test_the_documents_door_rows_are_renameable():
    """The endpoint existed for a long time with no caller at all — the only
    way to name an estimate was at the moment it was created."""
    src = _appjs()
    assert 'function renameEstimate' in src
    body = _fn_body(src, 'docEstimateListHtml', code_only=True)
    assert 'editable:true' in body.replace(' ', ''), \
        'the Documents door list must render its rows editable'
    row = _fn_body(src, 'estRowHtml')
    assert 'renameEstimate(' in row and 'cf-est-label-input' in row


def test_creating_an_estimate_lands_back_on_the_customer_screen():
    """newEstimateAction() renders the customer screen from a BLANK estimate,
    before the name is copied over, so it reads "No estimates yet" for a
    customer who has several. And setEstimateType('commercial') navigates to
    Scope on purpose. Either way the create flow has to re-land deliberately
    rather than assume it never moved."""
    body = _fn_body(_appjs(), 'docCreateEstimate', code_only=True)
    assert "switchPage('client')" in body


def test_renaming_an_unsaved_estimate_stays_in_memory():
    """No id yet means nothing on the server to patch — the name rides up with
    the first save instead, and the estimate must be marked dirty so that
    save actually happens."""
    body = _fn_body(_appjs(), 'renameEstimate', code_only=True)
    assert 'S.estimate_label = label' in body
    assert 'setDirty()' in body
    assert re.search(r'if \(estimateId\)', body), \
        'the server patch must be skipped when there is no id'


def test_renaming_patches_rather_than_saving_the_whole_estimate():
    body = _fn_body(_appjs(), 'renameEstimate', code_only=True)
    assert "/label" in body and "'PATCH'" in body
    assert 'saveEstimate(' not in body, \
        'a rename must not trigger a full-doc save'


def test_a_rename_updates_the_cached_row_it_was_typed_into():
    """_dashData backs the list being looked at; without this the row snaps
    back to the old name on the next redraw."""
    body = _fn_body(_appjs(), 'renameEstimate', code_only=True)
    assert '_dashData.find' in body and 'row.estimate_label = label' in body


def test_a_click_in_the_rename_box_does_not_load_a_different_estimate():
    """Every non-current row is a click target for doLoadEstimate. Clicking
    into its name box must edit it, not navigate away mid-rename."""
    row = _fn_body(_appjs(), 'estRowHtml')
    m = re.search(r'<input type="text" class="cf-est-label-input".*?>', row, re.S)
    assert m, 'the rename input is not where this test expects it'
    assert 'event.stopPropagation()' in m.group(0)


def test_every_estimate_type_gets_a_visible_icon_even_with_a_custom_label():
    """A custom label used to swallow the type entirely — an Insurance
    estimate with a label like "Storm Claim" looked identical to a Retail
    one. The icon must render regardless of whether a label is set."""
    body = _fn_body(_appjs(), 'estRowHtml')
    assert 'EST_TYPE_ICON[e.estimate_type]' in body
    assert 'cf-est-label' in body and 'icon' in body


# ── the customer file is reachable from the lists reps actually read ───────

def test_the_dashboard_row_opens_the_customer():
    """The file used to be reachable only from a home-screen search box and a
    sidebar button that appears once a name is typed, so a rep reading a list
    of estimates had no way to see that three of them are one customer."""
    body = _fn_body(_appjs(), 'dashRow')
    assert 'custEstimateCount' in body and 'openCustomer(' in body


def test_the_home_screen_rows_open_the_customer():
    src = _appjs()
    i = src.index('home-recents-hd')
    home = src[i:src.index('home-empty', i)]
    assert 'custEstimateCount' in home and 'openCustomer(' in home


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
