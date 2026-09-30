"""Attach an immutable Colorado hail report to an estimate and its customer documents."""
import copy
import io
import os
import tempfile
from flask import jsonify, request
from hail.property_routes import report_for_user
from hail.report_pdf import build


def preserve_evidence(data, existing):
    """Only the attachment endpoint may write evidence; ordinary saves toggle visibility."""
    existing = existing or {}
    data.pop('hail_reports', None)
    if existing.get('hail_reports'):
        data['hail_reports'] = copy.deepcopy(existing['hail_reports'])
    trusted = {a['id']: a for a in existing.get('attachments', []) if a.get('doc_type') == 'hail_report'}
    incoming = data.get('attachments') or []
    toggles = {a.get('id'): a.get('show_in_estimate', True) for a in incoming}
    labels = {a.get('id'): a.get('label') for a in incoming if isinstance(a.get('label'), str)}
    data['attachments'] = [a for a in incoming if a.get('id') not in trusted
                          and a.get('doc_type') != 'hail_report' and not a.get('hail_report_id')]
    for aid, attachment in trusted.items():
        att = copy.deepcopy(attachment)
        if aid in toggles:
            att['show_in_estimate'] = toggles[aid] is not False
        if aid in labels:
            att['label'] = labels[aid][:500]
        data['attachments'].append(att)


def register(api):
    @api.app.post('/api/estimates/<est_id>/hail-report')
    def attach_hail_report(est_id):
        est, error = api._condition_report_est_or_error(est_id)
        if error:
            return error
        data = request.get_json(silent=True) or {}
        report = report_for_user(str(data.get('report_id') or ''))
        if not report:
            return jsonify(error='Report not found'), 404
        if data.get('confirmed_property') is not True:
            return jsonify(error='Confirm that this report belongs to the estimate property.'), 400
        rid = report['id']
        filename = f'hail_{rid}.pdf'
        dest = os.path.join(api.UPLOADS_DIR, est_id)
        os.makedirs(dest, exist_ok=True)
        raw = build(report)
        # Stable filename/id make retries safe; the source report never changes.
        fd, temporary = tempfile.mkstemp(suffix='.pdf', dir=dest)
        try:
            with os.fdopen(fd, 'wb') as handle:
                handle.write(raw)
            os.replace(temporary, os.path.join(dest, filename))
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)
        pages = api._rasterize_pdf_pages(est_id, filename)
        attachment = {'id': 'hail_' + rid, 'filename': f'{est_id}/{filename}',
            'label': 'Colorado Hail History - ' + report['label'], 'doc_type': 'hail_report',
            'show_in_estimate': True, 'server_generated': True,
            'generated_at': report['created_at'], 'hail_report_id': rid, 'pages': pages or []}

        def apply(doc):
            if doc is None or not api._can_touch_estimate(doc):
                return None
            snapshots = doc.setdefault('hail_reports', [])
            if not any(r['id'] == rid for r in snapshots):
                saved = copy.deepcopy(report)
                saved['estimate_property_at_attachment'] = copy.deepcopy((doc.get('customer') or {}).get('address') or {})
                saved['estimate_project_address_at_attachment'] = doc.get('project_address') or ''
                snapshots.append(saved)
            attachments = doc.setdefault('attachments', [])
            if not any(a.get('id') == attachment['id'] for a in attachments):
                attachments.append(attachment)
            return doc

        updated = api.est_update(est_id, apply)
        if updated is None:
            return jsonify(error='Estimate changed or is no longer available.'), 409
        return jsonify(attachment=next(a for a in updated['attachments'] if a['id'] == attachment['id']),
                       hail_reports=updated['hail_reports'])


def append_to_pdf(raw, est):
    """Include selected hail evidence in downloaded/emailed estimate PDFs too."""
    reports = {r['id']: r for r in est.get('hail_reports') or []}
    selected = [reports[a['hail_report_id']] for a in est.get('attachments') or []
                if a.get('doc_type') == 'hail_report' and a.get('show_in_estimate', True)
                and a.get('hail_report_id') in reports]
    if not selected:
        return raw
    from pypdf import PdfReader, PdfWriter
    writer = PdfWriter()
    writer.append(PdfReader(io.BytesIO(raw)))
    for report in selected:
        writer.append(PdfReader(io.BytesIO(build(report))))
    out = io.BytesIO()
    writer.write(out)
    return out.getvalue()
