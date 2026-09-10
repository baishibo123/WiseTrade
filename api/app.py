"""
Service assembler.

    api/app.py  ──┬──► routers/bars.py     (feat/bar-service)
                  └──► routers/labels.py   (feat/label-store)

Created on `main` before the feature branches fork, and **not edited by them**.
Each branch fills exactly one router module, so two parallel branches never touch
the same file and the merge has no textual conflict to resolve. Adding a route
means adding it to your own router, never here.

This is the first service, not a chart backend. Routers are per domain rather
than per consumer for that reason: a second consumer of /bars should not need a
second endpoint.

Run:  uvicorn api.app:app --reload --port 8000
"""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from api.routers import bars, labels


def create_app() -> FastAPI:
    app = FastAPI(title="WiseTrade", version="0.1.0")

    # The UI dev server runs on its own port, so every browser request to this
    # process is cross-origin. Loopback only -- this service reads a local
    # database and has no authentication, and must not be reachable off-host.
    app.add_middleware(
        CORSMiddleware,
        allow_origin_regex=r"http://(localhost|127\.0\.0\.1):\d+",
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.include_router(bars.router)
    app.include_router(labels.router)
    return app


app = create_app()
