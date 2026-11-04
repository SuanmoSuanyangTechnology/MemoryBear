"""Use this checkout's profile service and avoid any installed src package."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
