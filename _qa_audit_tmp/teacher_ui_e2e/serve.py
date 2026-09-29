"""E2E test server: the same app as run.py, but threaded so an in-process PDF job
(USE_CELERY_FOR_INGESTION=false) does not block every other request during the suite.

    python _qa_audit_tmp/teacher_ui_e2e/serve.py 5055
"""
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)
os.environ.setdefault("TQDM_DISABLE", "1")
os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")

import threading  # noqa: E402

from run import app  # noqa: E402

# Local SQLite uses one StaticPool connection shared by every thread (app/utils/db.py), so concurrent
# requests corrupt each other's cursors. Serialize app requests (as run.py's threaded=False does) while
# still serving static files concurrently.
_db_lock = threading.Lock()
_inner_wsgi = app.wsgi_app


def _serialized_wsgi(environ, start_response):
    if environ.get("PATH_INFO", "").startswith(("/static/", "/teacher-static/")):
        return _inner_wsgi(environ, start_response)
    with _db_lock:
        return list(_inner_wsgi(environ, start_response))


app.wsgi_app = _serialized_wsgi

if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 5055
    app.run(host="127.0.0.1", port=port, debug=False, use_reloader=False, threaded=True)
