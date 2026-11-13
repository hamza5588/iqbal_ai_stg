"""Student heartbeat and admin-only usage summaries/downloads."""
from urllib.parse import urlsplit

from flask import Blueprint, jsonify, request, send_file, session

from app.utils.auth import login_required
from app.rbac.decorators import admin_only
from app.utils.db import get_db
from app.services.tutor_usage_service import record_heartbeat, daily_report, build_workbook

bp = Blueprint('tutor_usage', __name__)


@bp.after_request
def private_usage_response(response):
    response.headers['Cache-Control'] = 'no-store'
    return response


@bp.post('/api/tutor-usage/heartbeat')
@login_required
def heartbeat():
    origin = request.headers.get('Origin')
    if request.headers.get('Sec-Fetch-Site') == 'cross-site' or (origin and urlsplit(origin).netloc != request.host):
        return jsonify(error='Invalid request origin.'), 403
    if not request.is_json:
        return jsonify(error='JSON required.'), 400
    db = get_db()
    try:
        credited = record_heartbeat(db, session['user_id'], request.get_json(silent=True))
        db.commit()
        return jsonify(success=True, credited_ms=credited)
    except (ValueError, PermissionError) as exc:
        db.rollback()
        return jsonify(error=str(exc)), 403 if isinstance(exc, PermissionError) else 400
    except Exception:
        db.rollback()
        raise


@bp.get('/admin/daily-usage')
@login_required
@admin_only
def usage_report():
    try:
        page = int(request.args.get('page', '1'))
        if page < 1:
            raise ValueError('Invalid page.')
        report = daily_report(get_db(), request.args)
        report['page'], report['per_page'] = page, 50
        report['total_pages'] = (report['total'] + 49) // 50
        report['rows'] = report['rows'][(page - 1) * 50:page * 50]
        return jsonify(success=True, **report)
    except ValueError as exc:
        return jsonify(error=str(exc)), 400


@bp.get('/admin/daily-usage/export.xlsx')
@login_required
@admin_only
def usage_export():
    try:
        report = daily_report(get_db(), request.args)
        return send_file(build_workbook(report), as_attachment=True,
                         download_name=f"student-daily-usage-{report['start']}-to-{report['end']}.xlsx",
                         mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
    except ValueError as exc:
        return jsonify(error=str(exc)), 400
