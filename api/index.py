"""Vercel entry point: Vercel looks for an ASGI object named `app` in a root-level index.py.
Locally nothing uses this file (run.py / `uvicorn app.main:app` do)."""
from app.main import app  # noqa: F401
