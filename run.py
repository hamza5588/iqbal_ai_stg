import os
# Disable tqdm threading and tokenizer parallelism to prevent "cannot start new thread" errors
# This must be set BEFORE any imports that use these libraries
os.environ['TQDM_DISABLE'] = '1'
os.environ['TOKENIZERS_PARALLELISM'] = 'false'

from app import create_app
import logging
import sys

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)

app = create_app()

if __name__ == '__main__':
    # Get port from command line argument or default to 5000
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 5000
    
    # Enable debug mode but disable reloader to avoid permission issues in containers
    debug = os.environ.get('FLASK_DEBUG', '0') == '1'
    
    # Never use reloader in containerized environments
    app.run(debug=debug, host='0.0.0.0', port=port, use_reloader=False, threaded=False)