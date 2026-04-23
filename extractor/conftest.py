"""Pytest config: make `src.*` importable from extractor root."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
# Extractor root must come first so 'src' resolves to extractor/src, not classifier/src.
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
