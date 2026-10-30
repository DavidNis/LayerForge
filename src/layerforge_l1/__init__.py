"""LayerForge V3 L-1 feasibility package."""

from __future__ import annotations

import os
import sys
from pathlib import Path


if getattr(sys, "frozen", False):
    # Packaged Windows build (PyInstaller, see packaging/): bundled read-only resources
    # (fixtures/print_processes, app/static) live in the bundle folder; caches go to the
    # user's local application data, never into the application folder.
    PROJECT_ROOT = Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
    _CACHE = Path(os.environ.get("LOCALAPPDATA") or Path.home()) / "LayerForge" / "cache"
else:
    PROJECT_ROOT = Path(__file__).resolve().parents[2]
    _CACHE = PROJECT_ROOT / ".cache"
os.environ.setdefault("XDG_CACHE_HOME", str(_CACHE))
os.environ.setdefault("MPLCONFIGDIR", str(_CACHE / "matplotlib"))

__version__ = "0.1.0"

