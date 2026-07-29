"""The push pipe. Network stubbed — this is about chunking and error surfacing."""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from prospector import push as pushmod              # noqa: E402


class FakeResponse:
    def __init__(self, status, payload=None, text=''):
        self.status_code = status
        self._payload = payload or {}
        self.text = text

    def json(self):
        return self._payload


class FakeSession:
    """Records each request and replies with a canned counts payload."""

    def __init__(self, status=201):
        self.status = status
        self.calls = []

    def post(self, url, json=None, timeout=None):
        self.calls.append(json)
        n = len(json['rows'])
        return FakeResponse(self.status, {
            'batch': json.get('batch') or 'b1',
            'counts': {'inserted': n, 'duplicate': 0, 'suppressed': 0, 'invalid': 0},
            'details': [{'row': i, 'status': 'inserted'} for i in range(n)],
        })


def _rows(n):
    return [{'company': f'Co {i}', 'license_no': str(i)} for i in range(n)]


def test_push_sends_one_request_under_the_chunk_size():
    sess = FakeSession()
    res = pushmod.push(_rows(10), 'http://x', sess, lead_type='hoa', source='dora')
    assert len(sess.calls) == 1
    assert res['counts']['inserted'] == 10


def test_push_chunks_large_batches():
    """The importer caps a request at 5000; chunking keeps a failure cheap."""
    sess = FakeSession()
    res = pushmod.push(_rows(2500), 'http://x', sess, lead_type='hoa', source='dora')
    assert [len(c['rows']) for c in sess.calls] == [1000, 1000, 500]
    assert res['counts']['inserted'] == 2500


def test_push_forwards_the_import_options():
    sess = FakeSession()
    pushmod.push(_rows(1), 'http://x', sess, lead_type='hoa', source='dora',
                 assign='round_robin', batch='b-7', dry_run=True)
    sent = sess.calls[0]
    assert sent['lead_type'] == 'hoa' and sent['assign'] == 'round_robin'
    assert sent['batch'] == 'b-7' and sent['dry_run'] is True


def test_push_raises_on_a_rejected_import():
    sess = FakeSession(status=403)
    with pytest.raises(pushmod.PushError, match='403'):
        pushmod.push(_rows(1), 'http://x', sess, lead_type='hoa', source='dora')


def test_push_keeps_only_the_rows_that_did_not_land():
    """The interesting output is what was skipped, not the thousands that worked."""
    sess = FakeSession()
    res = pushmod.push(_rows(3), 'http://x', sess, lead_type='hoa', source='dora')
    assert res['not_inserted'] == []


def test_load_accepts_a_bare_row_list(tmp_path):
    import json
    p = tmp_path / 'rows.json'
    p.write_text(json.dumps([{'company': 'A'}]), encoding='utf-8')
    doc = pushmod.load(str(p))
    assert doc['rows'] == [{'company': 'A'}] and doc['lead_type'] == ''


def test_load_reads_a_pull_file(tmp_path):
    import json
    p = tmp_path / 'hoa.json'
    p.write_text(json.dumps({'source': 'dora:hoa', 'lead_type': 'hoa',
                             'rows': [{'company': 'A'}]}), encoding='utf-8')
    doc = pushmod.load(str(p))
    assert doc['lead_type'] == 'hoa' and doc['source'] == 'dora:hoa'


def test_sign_in_rejects_a_non_redirect(monkeypatch):
    """The portal redirects on success and re-renders the form on failure."""
    class S:
        def post(self, *a, **k):
            return FakeResponse(401)
    monkeypatch.setattr(pushmod.requests, 'Session', lambda: S())
    with pytest.raises(pushmod.PushError, match='Login failed'):
        pushmod.sign_in('http://x', 'luke', password='nope')
