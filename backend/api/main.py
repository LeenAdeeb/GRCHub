"""GRCHub HTTP API.

Run:
  DATABASE_URL=... GRCHUB_JWT_SECRET=<random string, 32+ chars> uvicorn backend.api.main:app --host 127.0.0.1

Every endpoint except /health and /auth/login needs a bearer token from /auth/login. Create the first
platform_admin with `python -m backend.cli user-create`. See docs/SECURITY.md for what is and is not protected.
"""
from __future__ import annotations

import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles

from backend.database.loader import connect
from backend.database.migrate import migrate
from backend.api import routes_auth, routes_org, routes_regulatory, routes_shared, routes_suggestions

@asynccontextmanager
async def lifespan(_: FastAPI):
    # Upgrade the schema when the software is upgraded (keeps existing data). Skipped when no database is configured.
    if os.environ.get("DATABASE_URL"):
        with connect() as conn:
            migrate(conn)
    yield


app = FastAPI(title="GRCHub API", version="0.3.0", lifespan=lifespan)
app.include_router(routes_auth.router)
app.include_router(routes_regulatory.router)
app.include_router(routes_org.router)
app.include_router(routes_shared.router)
app.include_router(routes_suggestions.router)


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}


# The web page (frontend/dist, built with `npm run build`) is served at /ui/.
UI_DIR = Path(__file__).resolve().parents[2] / "frontend" / "dist"
if UI_DIR.is_dir():
    app.mount("/ui", StaticFiles(directory=UI_DIR, html=True), name="ui")

    @app.get("/", include_in_schema=False)
    def root() -> RedirectResponse:
        return RedirectResponse("/ui/")
