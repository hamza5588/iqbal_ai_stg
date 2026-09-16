"""JSON response helpers for LMS API."""
import logging

from flask import g, jsonify

logger = logging.getLogger(__name__)


def json_success(data=None, status=200, message=None):
    payload = {"success": True, "data": data}
    if message:
        payload["message"] = message
    return jsonify(payload), status


def json_error(message: str, code: str = "error", status: int = 400, details=None):
    # LMS routes uniformly funnel "except Exception as e: return json_error(...)"
    # through here. If that exception came from a DB call, the request-scoped
    # session (g.db) can be left in a failed-transaction state; without a
    # rollback here, the *next* query on this same session -- later in this
    # same request, e.g. a subsequent service call -- fails too with
    # "current transaction is aborted", masking the real error. Best-effort:
    # never let a rollback failure hide the original error response.
    db = g.get('db', None)
    if db is not None:
        try:
            db.rollback()
        except Exception:
            logger.error("Failed to roll back DB session in json_error", exc_info=True)

    payload = {"success": False, "error": {"code": code, "message": message}}
    if details is not None:
        payload["error"]["details"] = details
    return jsonify(payload), status
