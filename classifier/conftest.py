"""Pytest bootstrap: make classifier/ importable so tests can do
`from src.classifier import ...`."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
