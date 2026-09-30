"""Authenticated property search, confirmation and report endpoints."""
import io
from flask import Blueprint, current_app, jsonify, request, send_file, session
from itsdangerous import URLSafeTimedSerializer, BadSignature
from hail import property as prop
from portal import users


def identity():
    name = session.get('username') or session.get('user')
    return name if name and users.get(name) else None


def report_for_user(report_id):
    name = identity()
    return prop.load(report_id, name, users.is_manager_up(name)) if name else None


def register(app, http):
    bp = Blueprint('property_hail', __name__)

    @bp.before_request
    def auth():
        if not identity():
            return jsonify(error='Unauthorized'), 401

    def signer():
        return URLSafeTimedSerializer(current_app.secret_key, salt='colorado-hail-address-v1')

    @bp.get('/api/hail/properties')
    def search():
        try:
            rows = prop.candidates(http, request.args.get('q'))
            for row in rows:
                row['token'] = signer().dumps({'owner': identity(), 'candidate': dict(row)})
            return jsonify(candidates=rows)
        except ValueError as exc:
            return jsonify(error=str(exc)), 400
        except Exception:
            return jsonify(error='Address search is busy or unavailable. Please try again.'), 503

    @bp.post('/api/hail/reports')
    def create():
        data = request.get_json(silent=True) or {}
        if data.get('confirmed') is not True:
            return jsonify(error='Confirm the address and map pin first.'), 400
        try:
            signed = signer().loads(data.get('token', ''), max_age=3600)
            if signed['owner'] != identity():
                return jsonify(error='Search this address again under your own account.'), 403
            candidate = signed['candidate']
            report = prop.snapshot(candidate, data.get('lat'), data.get('lng'), data.get('days', 1825))
            prop.save(report, identity())
            return jsonify(report), 201
        except BadSignature:
            return jsonify(error='Address confirmation expired. Search the address again.'), 400
        except (ValueError, TypeError, KeyError) as exc:
            return jsonify(error=str(exc)), 400

    @bp.get('/api/hail/reports/<report_id>')
    def read(report_id):
        report = report_for_user(report_id)
        return jsonify(report) if report else (jsonify(error='Report not found'), 404)

    @bp.get('/api/hail/reports/<report_id>/pdf')
    def pdf(report_id):
        report = report_for_user(report_id)
        if not report:
            return jsonify(error='Report not found'), 404
        from hail.report_pdf import build
        return send_file(io.BytesIO(build(report)), mimetype='application/pdf',
                         download_name=f'Colorado-Hail-{report_id[:8]}.pdf')

    app.register_blueprint(bp)
