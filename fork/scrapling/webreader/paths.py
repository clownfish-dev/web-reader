"""Where this fork keeps its files. Kept apart from browser.py so the stdio shim starts without
importing the browser stack (patchright, curl_cffi)."""

import os
from pathlib import Path

ROOT = Path(os.environ.get("WEB_READER_HOME") or Path(__file__).resolve().parents[3])
