"""Start the PhishAware development server:  python run.py"""

import os

from src.app import create_app

app = create_app()

if __name__ == "__main__":
    port = int(os.environ.get("PORT", "5000"))
    debug = os.environ.get("FLASK_DEBUG") == "1"
    print(f"PhishAware is running at http://127.0.0.1:{port}  (press CTRL+C to stop)")
    app.run(host="127.0.0.1", port=port, debug=debug)
