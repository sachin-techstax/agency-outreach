"""Pytest bootstrap.

Ensures the project root (containing the ``app`` package) is importable when
pytest is invoked from inside the Docker container at ``/workspace``.
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
