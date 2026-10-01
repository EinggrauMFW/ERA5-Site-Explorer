import os
import sys
import tempfile
from pathlib import Path

# Must be set before app.py is imported: it creates the downloads folder on import.
os.environ.setdefault("DOWNLOADS_DIR", tempfile.mkdtemp(prefix="ecmwf-app-tests-"))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
