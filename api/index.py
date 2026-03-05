import sys
import os

# Add project root to path so app.py and its imports are found
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from app import app  # noqa: F401  — Vercel uses this as the WSGI entry point
