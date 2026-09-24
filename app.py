import os
import sys
from pathlib import Path


PROJECT_DIR = Path(__file__).resolve().parent / "job portal" / "job portal"
sys.path.insert(0, str(PROJECT_DIR))

from backend.app import app


if __name__ == "__main__":
    app.run(
        debug=os.environ.get("FLASK_DEBUG", "0") == "1",
        host="0.0.0.0",
        port=int(os.environ.get("PORT", "5000")),
    )
