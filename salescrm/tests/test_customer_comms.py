"""The CRM finally says something to the customer.

It owned a homeowner from the door knock to the signature and sent them nothing
in that whole window — not a decision, just that the only mailer in the repo
lived inside estimator/app.py. The gap that costs money is the appointment: it
gets booked and the customer hears nothing until somebody knocks.
"""
import app as appmod
import pytest
from conftest import signup, login, new_lead
from portal import mail as pmail


@pytest.fixture
def outbox(monkeypatch):
    """Capture sends instead of making them, and pretend mail is configured."""
    sent = []

    def fake_send(subject, html, to, cc=None, attachments=None, bcc=None):
        sent.append({'subject': subject, 'html': html, 'to': to, 'bcc': bcc})
        return True

    monkeypatch.setattr(pmail, 'send', fake_send)
    monkeypatch.setattr(pmail, 'configured', lambda: True)
    monkeypatch.setattr(appmod.pmail, 'send', fake_send)
    monkeypatch.setattr(appmod.pmail, 'configured', lambda: True)
    return sent


def _at(hours):
    return appmod._iso(appmod._now_dt() + appmod.timedelta(hours=hours))


def _book(client, lead_id, when):
    return client.patch(f'/api/leads/{lead_id}/stage',
                        json={'stage': 'appt_set', 'appt_at': when}).get_json()


# ── Booking tells the customer ───────────────────────────────────────────────

def test_booking_emails_the_customer(client, outbox):
    signup(client)
    lead = new_lead(client, first_name='Dana', email='dana@example.com')
    _book(client, lead['id'], _at(48))
    assert len(outbox) == 1
    assert outbox[0]['to'] == 'dana@example.com'
    assert 'confirmed' in outbox[0]['subject'].lower()
    assert 'Dana' in outbox[0]['html']


def test_the_rep_is_copied_so_they_know_what_was_said(client, outbox):
    signup(client)
    lead = new_lead(client, email='dana@example.com')
    _book(client, lead['id'], _at(48))
    assert outbox[0]['bcc'] == 'luke@projectoneroofing.com'


def test_the_send_is_on_the_timeline(client, outbox):
    signup(client)
    lead = new_lead(client, email='dana@example.com')
    _book(client, lead['id'], _at(48))
    acts = client.get(f'/api/leads/{lead["id"]}').get_json()['activities']
    assert any('Confirmation emailed' in a['body'] for a in acts)


def test_confirming_is_once_per_appointment_time(client, outbox):
    signup(client)
    lead = new_lead(client, email='dana@example.com')
    when = _at(48)
    _book(client, lead['id'], when)
    client.put(f'/api/leads/{lead["id"]}', json={'city': 'Loveland'})
    assert len(outbox) == 1


def test_rescheduling_sends_a_fresh_confirmation(client, outbox):
    """The columns store the appointment TIME, not a flag, so a reschedule
    invalidates itself — a boolean would have confirmed the first time forever
    and left the customer holding the wrong one."""
    signup(client)
    lead = new_lead(client, email='dana@example.com')
    _book(client, lead['id'], _at(48))
    client.put(f'/api/leads/{lead["id"]}', json={'appt_at': _at(72)})
    assert len(outbox) == 2
    assert outbox[1]['subject'] != outbox[0]['subject']


# ── When it must NOT send ────────────────────────────────────────────────────

def test_no_email_address_means_no_send(client, outbox):
    signup(client)
    lead = new_lead(client, email='')
    got = _book(client, lead['id'], _at(48))
    assert outbox == []
    assert got['appt_reachable'] is False, 'and the drawer says so'


def test_an_opted_out_customer_is_not_emailed(client, outbox):
    signup(client)
    lead = new_lead(client, email='dana@example.com')
    client.put(f'/api/leads/{lead["id"]}', json={'dnc': 1})
    _book(client, lead['id'], _at(48))
    assert outbox == []


def test_a_past_appointment_is_not_confirmed(client, outbox):
    signup(client)
    lead = new_lead(client, email='dana@example.com')
    _book(client, lead['id'], _at(-5))
    assert outbox == []


def test_unconfigured_mail_sends_nothing_and_claims_nothing(client, monkeypatch):
    """The booking still works. What must not happen is the lead recording a
    confirmation the customer never got."""
    monkeypatch.setattr(appmod.pmail, 'configured', lambda: False)
    signup(client)
    lead = new_lead(client, email='dana@example.com')
    got = _book(client, lead['id'], _at(48))
    assert got['appt_at'] and got['appt_confirmed'] is False


def test_a_failed_send_is_retried_not_recorded(client, monkeypatch):
    """A customer who never got the confirmation must not be recorded as having
    had one — the claim is released so the next pass tries again."""
    monkeypatch.setattr(appmod.pmail, 'configured', lambda: True)
    monkeypatch.setattr(appmod.pmail, 'send',
                        lambda *a, **k: False)
    signup(client)
    lead = new_lead(client, email='dana@example.com')
    assert _book(client, lead['id'], _at(48))['appt_confirmed'] is False


# ── The day-before reminder ──────────────────────────────────────────────────

def test_tomorrows_appointment_gets_a_reminder(client, outbox):
    signup(client)
    lead = new_lead(client, email='dana@example.com')
    _book(client, lead['id'], _at(20))
    outbox.clear()
    assert appmod._check_appt_reminders() == 1
    assert 'Reminder' in outbox[0]['subject']


def test_next_week_gets_no_reminder_yet(client, outbox):
    signup(client)
    lead = new_lead(client, email='dana@example.com')
    _book(client, lead['id'], _at(24 * 6))
    outbox.clear()
    assert appmod._check_appt_reminders() == 0


def test_the_reminder_goes_out_once(client, outbox):
    signup(client)
    lead = new_lead(client, email='dana@example.com')
    _book(client, lead['id'], _at(20))
    outbox.clear()
    appmod._check_appt_reminders()
    assert appmod._check_appt_reminders() == 0
    assert len(outbox) == 1


def test_a_reschedule_re_arms_the_reminder(client, outbox):
    signup(client)
    lead = new_lead(client, email='dana@example.com')
    _book(client, lead['id'], _at(20))
    appmod._check_appt_reminders()
    outbox.clear()
    client.put(f'/api/leads/{lead["id"]}', json={'appt_at': _at(23)})
    assert appmod._check_appt_reminders() == 1


def test_a_won_deal_is_not_reminded(client, outbox):
    signup(client)
    lead = new_lead(client, email='dana@example.com')
    _book(client, lead['id'], _at(20))
    client.patch(f'/api/leads/{lead["id"]}/stage', json={'stage': 'won'})
    outbox.clear()
    assert appmod._check_appt_reminders() == 0


def test_the_email_carries_the_four_facts_a_homeowner_needs(client, outbox):
    """When, who, where, and how to move it."""
    signup(client)
    lead = new_lead(client, first_name='Dana', email='dana@example.com',
                    address='12 Elm St', city='Fort Collins')
    _book(client, lead['id'], _at(48))
    html = outbox[0]['html']
    assert '12 Elm St' in html and 'Fort Collins' in html
    assert 'Luke' in html
    assert 'reply' in html.lower()


def test_customer_names_are_escaped(client, outbox):
    signup(client)
    lead = new_lead(client, first_name='<script>x</script>',
                    email='dana@example.com')
    _book(client, lead['id'], _at(48))
    assert '<script>' not in outbox[0]['html']
