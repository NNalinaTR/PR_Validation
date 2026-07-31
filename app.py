"""
Server Setter Automation - Flask Web Application
Provides a web UI for checking server health via Gotmon
"""

import os
import yaml
import logging
from flask import Flask, render_template, request, jsonify
from gotmon_checker import GotmonChecker

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

app = Flask(__name__)

# Load configuration
CONFIG_PATH = os.path.join(os.path.dirname(__file__), 'config.yaml')

def load_config():
    """Load configuration from YAML file."""
    try:
        with open(CONFIG_PATH, 'r') as f:
            return yaml.safe_load(f)
    except Exception as e:
        logger.error(f"Error loading config: {e}")
        return {}


@app.route('/')
def index():
    """Render the main page with server input form."""
    return render_template('index.html')


@app.route('/check', methods=['POST'])
def check_server():
    """
    API endpoint to check server health.

    Expected JSON body:
    {
        "server_name": "c835uvdctxdr",
        "inc_number": "INC0012345" (optional)
    }
    """
    try:
        data = request.get_json()

        if not data or 'server_name' not in data:
            return jsonify({'error': 'server_name is required'}), 400

        server_name = data['server_name'].strip()
        inc_number = data.get('inc_number', '').strip()

        if not server_name:
            return jsonify({'error': 'server_name cannot be empty'}), 400

        logger.info(f"Checking server: {server_name}, INC: {inc_number}")

        # Load config and run check
        config = load_config()
        checker = GotmonChecker(config)
        result = checker.check_server(server_name)

        # Add INC number to result for reference
        result['inc_number'] = inc_number

        logger.info(f"Check complete for {server_name}: {result.get('action')}")

        return jsonify(result)

    except Exception as e:
        logger.error(f"Error in /check endpoint: {e}")
        return jsonify({'error': str(e)}), 500


@app.route('/config', methods=['GET'])
def get_config():
    """Return current configuration (excluding sensitive data)."""
    config = load_config()
    # Remove any sensitive data before returning
    safe_config = {
        'gotmon_url': config.get('gotmon', {}).get('url'),
        'cpu_threshold': config.get('thresholds', {}).get('cpu_max_percentage'),
        'headless': config.get('browser', {}).get('headless'),
    }
    return jsonify(safe_config)


@app.route('/health', methods=['GET'])
def health_check():
    """Health check endpoint."""
    return jsonify({'status': 'healthy'})


if __name__ == '__main__':
    print("\n" + "="*50)
    print("  Server Setter Automation")
    print("="*50)
    print("\n  Open your browser and go to:")
    print("  http://localhost:5000")
    print("\n" + "="*50 + "\n")

    app.run(debug=True, host='0.0.0.0', port=5000)
