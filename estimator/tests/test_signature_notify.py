"""Who hears that a contract was signed.

The rep on the estimate gets it and the owner is bcc'd on every one, whoever
sold it. An estimate with no rep used to tell nobody at all, and a legacy
free-typed salesperson ("Luke Durnell") built an address SendGrid rejects —
taking the owner's copy down with it, since one bad recipient fails the whole
request.
"""
import pytest

import app as A


@pytest.fixture
def outbox(monkeypatch):
    sent = []

    def _fake(subject, html, to_addr, cc=None, attachments=None, bcc=None):
        sent.append({'subject': subject, 'to': to_addr, 'cc': cc, 'bcc': bcc})
        return True

    monkeypatch.setattr(A, '_send_email', _fake)
    monkeypatch.delenv('OWNER_NOTIFY_EMAIL', raising=False)
    monkeypatch.delenv('NOTIFY_CC', raising=False)
    return sent


def _est(salesperson='bryan'):
    return {
        'estimate_id': 's1', 'salesperson': salesperson,
        'estimate_type': 'retail', 'share_token': 'tok-s1',
        'customer': {'name': 'Jon Smith', 'address': {'city': 'Loveland'}},
        'pricing': {'mode': 'margin'},
        'signature': {'name': 'Jon Smith', 'signed_at': '2026-09-01T15:04:05Z'},
        'trades': {'roofing': {
            'enabled': True, 'mode': 'simple',
            'line_items': [{'name': 'Roof', 'quantity': 1,
                            'unit_price': 24000.0, 'unit_cost': 15000.0}],
        }},
    }


def test_the_rep_gets_it_and_the_owner_is_copied_by_default(outbox):
    A.send_signature_notification(_est())
    assert outbox[0]['to'] == 'bryan@projectoneroofing.com'
    assert outbox[0]['bcc'] == 'luke@projectoneroofing.com'


def test_no_rep_goes_to_the_owner_instead_of_nobody(outbox):
    A.send_signature_notification(_est(salesperson=''))
    assert len(outbox) == 1
    assert outbox[0]['to'] == 'luke@projectoneroofing.com'


def test_a_free_typed_name_is_not_turned_into_an_address(outbox):
    A.send_signature_notification(_est(salesperson='Luke Durnell'))
    assert outbox[0]['to'] == 'luke@projectoneroofing.com'


def test_the_variable_overrides_and_empty_switches_it_off(outbox, monkeypatch):
    monkeypatch.setenv('OWNER_NOTIFY_EMAIL', 'office@projectoneroofing.com')
    A.send_signature_notification(_est())
    assert outbox[-1]['bcc'] == 'office@projectoneroofing.com'
    monkeypatch.setenv('OWNER_NOTIFY_EMAIL', '')
    A.send_signature_notification(_est())
    assert outbox[-1]['bcc'] is None


def test_change_orders_follow_the_same_rule(outbox):
    co = {'number': 'CO-1', 'share_token': 't', 'line_items': [],
          'signature': {'name': 'Jon Smith'}}
    A.send_co_signature_notification(_est(salesperson=''), co)
    assert outbox[0]['to'] == 'luke@projectoneroofing.com'
