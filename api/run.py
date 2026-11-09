"""Dev launcher:  python run.py [--port 8000] [--reload]

psycopg's async mode cannot run on Windows' default Proactor event loop, and uvicorn creates its loop before it
imports the app, so the selector policy has to be set here, first. (On Linux/macOS plain `uvicorn app.main:app` works.)"""
import argparse
import asyncio
import sys

if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

import uvicorn  # noqa: E402

if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8000)
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--reload", action="store_true")
    a = ap.parse_args()
    uvicorn.run("app.main:app", host=a.host, port=a.port, reload=a.reload, loop="none")
