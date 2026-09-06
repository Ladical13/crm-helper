"""Outbound mail — one path, shared by every app that has to reach a person.

Lived inside `estimator/app.py`, which meant the estimator could email a
customer and the CRM could not. That is why the CRM has never spoken to a
homeowner: not a decision, just where the function happened to sit. It moves
here for the same reason `funnel.py`, `geo.py` and `lost_reasons.py` did — the
apps keep separate databases and anything genuinely shared needs a home
belonging to none of them.

Two properties callers depend on and must not lose:

**It never raises.** Delivery is somebody else's network. A failed send returns
False and logs; it never takes down the request that triggered it, because
every caller here is doing something else important — signing a contract,
saving a lead — and mail is the side effect, never the transaction.

**It prefers the SendGrid HTTP API over SMTP.** Railway blocks outbound SMTP
ports, so on the box this actually runs on, SMTP is the fallback that usually
cannot connect. SendGrid's SMTP login uses the literal username `apikey` with
the key as the password, which is why `SMTP_PASS` doubles as the API key when
`SENDGRID_API_KEY` is not set separately.
"""
import os
import smtplib
from email.mime.application import MIMEApplication
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

try:
    import requests as http
except Exception:                    # requests is optional; SMTP still works
    http = None


def configured():
    """True when any delivery path is available — SendGrid API or SMTP.

    Callers gate scheduled jobs on this. SMTP_HOST alone is deliberately not
    required: the API path needs no host.
    """
    return bool(os.environ.get('SENDGRID_API_KEY', '').strip()
                or os.environ.get('SMTP_HOST', '').strip()
                or (os.environ.get('SMTP_USER', '').strip() == 'apikey'
                    and os.environ.get('SMTP_PASS', '').strip()))


def send(subject, html_body, to_addr, cc=None, attachments=None, bcc=None):
    """Send an HTML email. Prefers the SendGrid HTTP API (HTTPS/443), which works
    on hosts that block outbound SMTP ports like Railway; falls back to SMTP when
    no API key is available. Logs errors, never raises.
    attachments: list of (filename, bytes) tuples."""
    if not to_addr:
        return False

    # Don't BCC the primary recipient — SendGrid would silently drop the duplicate,
    # but the intent ("I'm already getting this") is clearer this way.
    if bcc and to_addr and bcc.strip().lower() == to_addr.strip().lower():
        bcc = None

    # Prefer the SendGrid Web API when we have a key. SendGrid's SMTP login uses
    # the literal username "apikey" and the API key as the password, so we can
    # reuse SMTP_PASS as the API key when SENDGRID_API_KEY isn't set explicitly.
    api_key = os.environ.get('SENDGRID_API_KEY', '').strip()
    if not api_key and os.environ.get('SMTP_USER', '').strip() == 'apikey':
        api_key = os.environ.get('SMTP_PASS', '').strip()
    if api_key and http is not None:
        if _via_sendgrid_api(api_key, subject, html_body, to_addr, cc, attachments, bcc):
            return True
        # API failed — try SMTP as a last resort (may also be blocked)
    return _via_smtp(subject, html_body, to_addr, cc, attachments, bcc)


def _via_sendgrid_api(api_key, subject, html_body, to_addr, cc=None, attachments=None, bcc=None):
    """Send through SendGrid's v3 HTTP API over HTTPS. Returns True on success."""
    from email.utils import parseaddr
    smtp_from = (os.environ.get('SMTP_FROM') or os.environ.get('SMTP_USER') or '').strip()
    from_name, from_email = parseaddr(smtp_from)
    if not from_email:
        # Fallback so SendGrid doesn't reject the request due to missing sender
        from_email = 'noreply@projectoneroofing.com'
        from_name  = 'Project One Roofing'

    personalization = {'to': [{'email': to_addr}]}
    if cc:
        cc_list = [{'email': x.strip()} for x in cc.split(',') if x.strip()]
        if cc_list:
            personalization['cc'] = cc_list
    if bcc:
        bcc_list = [{'email': x.strip()} for x in bcc.split(',') if x.strip()]
        if bcc_list:
            personalization['bcc'] = bcc_list

    payload = {
        'personalizations': [personalization],
        'from': {'email': from_email, 'name': from_name or 'Project One Roofing'},
        'subject': subject,
        'content': [{'type': 'text/html', 'value': html_body}],
    }
    if attachments:
        import base64
        payload['attachments'] = [{
            'content':     base64.b64encode(data).decode('ascii'),
            'filename':    fname,
            'type':        'application/pdf',
            'disposition': 'attachment',
        } for fname, data in attachments]

    try:
        resp = http.post('https://api.sendgrid.com/v3/mail/send',
                         json=payload,
                         headers={'Authorization': f'Bearer {api_key}',
                                  'Content-Type': 'application/json'},
                         timeout=15)
        if resp.status_code in (200, 201, 202):
            print(f'[email] Sent "{subject}" to {to_addr} via SendGrid API')
            return True
        print(f'[email] SendGrid API rejected "{subject}" to {to_addr}: '
              f'{resp.status_code} {resp.text[:300]}')
        return False
    except Exception as exc:
        print(f'[email] SendGrid API error for "{subject}" to {to_addr}: {exc}')
        return False


def _via_smtp(subject, html_body, to_addr, cc=None, attachments=None, bcc=None):
    """Send an HTML email via configured SMTP. Logs errors, never raises."""
    smtp_host = os.environ.get('SMTP_HOST', '').strip()
    if not smtp_host or not to_addr:
        return False
    smtp_port = int(os.environ.get('SMTP_PORT', '587'))
    smtp_user = os.environ.get('SMTP_USER', '').strip()
    smtp_pass = os.environ.get('SMTP_PASS', '').strip()
    smtp_from = os.environ.get('SMTP_FROM', smtp_user).strip() or smtp_user

    msg = MIMEMultipart('mixed' if attachments else 'alternative')
    msg['Subject'] = subject
    msg['From']    = smtp_from
    msg['To']      = to_addr
    recipients     = [to_addr]
    if cc:
        msg['Cc'] = cc
        recipients += [x.strip() for x in cc.split(',') if x.strip()]
    if bcc:
        # Deliberately NOT setting msg['Bcc'] — the whole point is that other
        # recipients can't see it. Just add to the envelope.
        recipients += [x.strip() for x in bcc.split(',') if x.strip()]
    msg.attach(MIMEText(html_body, 'html'))
    for fname, data in (attachments or []):
        part = MIMEApplication(data, Name=fname)
        part['Content-Disposition'] = f'attachment; filename="{fname}"'
        msg.attach(part)
    try:
        with smtplib.SMTP(smtp_host, smtp_port, timeout=10) as srv:
            srv.ehlo()
            srv.starttls()
            if smtp_user and smtp_pass:
                srv.login(smtp_user, smtp_pass)
            srv.sendmail(smtp_from, recipients, msg.as_string())
        print(f'[email] Sent "{subject}" to {to_addr} via SMTP')
        return True
    except Exception as exc:
        print(f'[email] Failed to send "{subject}" to {to_addr} via SMTP: {exc}')
        return False
