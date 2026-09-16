"""Marketing Studio endpoints on Nimbus's existing admin-only blueprint."""
from functools import wraps

from flask import Response, jsonify, request, send_from_directory, session

from agents.content import studio


def register(bp, start_job):
    def api(fn):
        @wraps(fn)
        def wrapped(*args, **kwargs):
            try:
                return fn(*args, **kwargs)
            except studio.Conflict as exc:
                return jsonify(error=str(exc)), 409
            except LookupError as exc:
                return jsonify(error=str(exc)), 404
            except (ValueError, TypeError) as exc:
                return jsonify(error=str(exc)), 400
        return wrapped

    def body():
        data = request.get_json(silent=True)
        if not isinstance(data, dict):
            raise ValueError('Expected a JSON object')
        return data

    @bp.route('/assets/studio.<extension>')
    def studio_asset(extension):
        if extension not in ('js', 'css'):
            return '', 404
        from pathlib import Path
        return send_from_directory(Path(__file__).parent / 'static' / 'nimbus', f'studio.{extension}')

    @bp.route('/api/studio/campaigns', methods=['GET', 'POST'])
    @api
    def studio_campaigns():
        if request.method == 'POST':
            return jsonify(id=studio.create_campaign(body())), 201
        return jsonify(campaigns=studio.campaigns(), platforms=studio.posts.PLATFORMS,
                       services=studio.SERVICES, audiences=studio.AUDIENCES)

    @bp.route('/api/studio/campaigns/<int:campaign_id>')
    @api
    def studio_campaign(campaign_id):
        return jsonify(campaign=studio.campaign(campaign_id), ideas=studio.ideas(campaign_id),
                       posts=studio.list_posts(campaign_id), results=studio.results(campaign_id))

    @bp.route('/api/studio/campaigns/<int:campaign_id>/research', methods=['POST'])
    @api
    def studio_research(campaign_id):
        data = body()
        live = data.get('live', False)
        if type(live) is not bool:
            raise ValueError('live must be true or false')
        studio.campaign(campaign_id)
        return start_job('studio-research', lambda: studio.research(campaign_id, live), False)

    @bp.route('/api/studio/campaigns/<int:campaign_id>/ideas/<int:idea_id>', methods=['POST'])
    @api
    def studio_idea(campaign_id, idea_id):
        studio.select_idea(campaign_id, idea_id, body())
        return jsonify(ok=True)

    @bp.route('/api/studio/campaigns/<int:campaign_id>/generate', methods=['POST'])
    @api
    def studio_generate(campaign_id):
        data = body()
        manual = data.get('manual', False)
        if type(manual) is not bool:
            raise ValueError('manual must be true or false')
        studio.campaign(campaign_id)
        selected = [i for i in studio.ideas(campaign_id) if i['status'] == 'selected']
        if not 1 <= len(selected) <= 7:
            raise ValueError('Select between one and seven ideas')
        return start_job('studio-drafts', lambda: studio.generate(campaign_id, manual), False)

    @bp.route('/api/studio/campaigns/<int:campaign_id>/suggest', methods=['POST'])
    @api
    def studio_suggest(campaign_id):
        studio.campaign(campaign_id)
        return jsonify(studio.suggest_week(campaign_id))

    @bp.route('/api/studio/posts/<int:draft_id>', methods=['POST'])
    @api
    def studio_update(draft_id):
        return jsonify(studio.update_post(draft_id, body(), session.get('username', '')))

    @bp.route('/api/studio/posts/<int:draft_id>/history')
    @api
    def studio_history(draft_id):
        return jsonify(history=studio.history(draft_id))

    @bp.route('/api/studio/campaigns/<int:campaign_id>/export')
    @api
    def studio_export(campaign_id):
        studio.campaign(campaign_id)
        if request.args.get('format') == 'json':
            return jsonify(campaign=studio.campaign(campaign_id), ideas=studio.ideas(campaign_id),
                           posts=studio.list_posts(campaign_id), results=studio.results(campaign_id))
        return Response(studio.export_csv(campaign_id), mimetype='text/csv', headers={
            'Content-Disposition': f'attachment; filename="nimbus-plan-{campaign_id}.csv"'})
