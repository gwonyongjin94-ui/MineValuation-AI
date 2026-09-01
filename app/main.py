from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.analysis import router as analysis_router
from app.api.health import router as health_router
from app.config import get_settings

app = FastAPI(title="MineValuation-AI")
app.include_router(health_router)
app.include_router(analysis_router)

# The GitHub Pages frontend (site/) is a static site on a different origin
# than wherever this API ends up deployed, so it needs CORS - browsers
# block cross-origin fetch() by default otherwise. Configurable via
# ALLOWED_ORIGINS (comma-separated) rather than hardcoded, since the
# Pages URL depends on the repo owner/name and the deployed API's own
# URL depends on whichever host runs it. Local dev ports are allowed
# unconditionally (8000: this API via `python site` launch config's
# uvicorn; 8080: `python -m http.server 8080 --directory site`, matching
# .claude/launch.json's "site" entry) since neither is ever a real
# deployment target.
_configured_origins = [o.strip() for o in get_settings().allowed_origins.split(",") if o.strip()]
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        *_configured_origins,
        "http://localhost:8000",
        "http://127.0.0.1:8000",
        "http://localhost:8080",
        "http://127.0.0.1:8080",
    ],
    allow_methods=["GET", "POST"],
    allow_headers=["Content-Type"],
)
