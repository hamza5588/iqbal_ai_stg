import os
# Disable tqdm threading and tokenizer parallelism to prevent "cannot start new thread" errors
# This must be set BEFORE any imports that use these libraries
os.environ['TQDM_DISABLE'] = '1'
os.environ['TOKENIZERS_PARALLELISM'] = 'false'

from app import create_app
import logging
import argparse

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)

app = create_app()

# Eager-load Whisper + RAG embeddings once per Gunicorn worker (or dev server process).
# See app.utils.ml_warmup for env flags (WARMUP_ML_ON_START, etc.).
try:
    from app.utils.ml_warmup import warmup_http_worker_models

    warmup_http_worker_models()
except Exception:
    import logging as _logging

    _logging.getLogger(__name__).exception("ML warmup at process start failed")

if __name__ == '__main__':
    # Accept either:
    #   python run.py 5000
    # or:
    #   python run.py --port 5000
    parser = argparse.ArgumentParser()
    parser.add_argument('port', nargs='?', default=5000, type=int)
    parser.add_argument('--port', dest='port_opt', type=int, default=None)
    args = parser.parse_args()
    port = args.port_opt if args.port_opt is not None else args.port
    
    # Enable debug mode
    debug = os.environ.get('FLASK_DEBUG', '0') == '1'
    
    # Enable reloader in debug mode if not explicitly disabled
    use_reloader = debug and os.environ.get('FLASK_USE_RELOADER', '1') == '1'
    
    app.run(debug=debug, host='0.0.0.0', port=port, use_reloader=use_reloader, threaded=False)