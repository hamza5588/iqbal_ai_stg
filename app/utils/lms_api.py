"""JSON response helpers for LMS API."""
from flask import jsonify


def json_success(data=None, status=200, message=None):
    payload = {"success": True, "data": data}
    if message:
        payload["message"] = message
    return jsonify(payload), status


def json_error(message: str, code: str = "error", status: int = 400, details=None):
    payload = {"success": False, "error": {"code": code, "message": message}}
    if details is not None:
        payload["error"]["details"] = details
    return jsonify(payload), status
