import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from app.api import connections, meta, sessions, settings
from app.config import BASE_DIR, get_settings
from app.db import check_db, init_db, reset_engine
from app.services.cortex.base import CortexError
from app.services.metadata.base import MetadataError
from app.services.planner import PlanError

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logging.getLogger("alembic.runtime.plugins").setLevel(logging.WARNING)
log = logging.getLogger("app")
STATIC_DIR = BASE_DIR / "static"

# The frontend sends this on every request that changes data. Other websites can't add custom headers
# to cross-site requests without a CORS preflight (which this app never allows), so this blocks CSRF.
APP_HEADER = ("x-requested-with", "looker-helper")
SECURITY_HEADERS = {
    "Content-Security-Policy": (
        "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; "
        "connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'"
    ),
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "same-origin",
    "Permissions-Policy": "camera=(), microphone=(), geolocation=()",
}


def _warn_about_sign_in() -> None:
    s = get_settings()
    if s.iap_audience:
        log.info("Sign-in: verifying IAP tokens")
        return
    if s.dev_user_email:
        log.warning("LH_DEV_USER_EMAIL is set: requests without a sign-in header are treated as %s. "
                    "Use this for local development only.", s.dev_user_email)
    log.warning("Sign-in: trusting the %s header. Make sure only your sign-in proxy can reach this app, "
                "or set LH_IAP_AUDIENCE to verify IAP tokens.", s.auth_header)


@asynccontextmanager
async def lifespan(_: FastAPI):
    _warn_about_sign_in()
    init_db()
    yield
    reset_engine()


_docs = get_settings().enable_api_docs
app = FastAPI(title="Looker Helper", version="1.0.0", lifespan=lifespan,
              docs_url="/docs" if _docs else None, redoc_url=None, openapi_url="/openapi.json" if _docs else None)


@app.middleware("http")
async def security(request: Request, call_next):
    if (request.url.path.startswith("/api/") and request.method not in ("GET", "HEAD", "OPTIONS")
            and request.headers.get(APP_HEADER[0]) != APP_HEADER[1]):
        return JSONResponse(status_code=403, content={"detail": "Request blocked. Please use the Looker Helper app."})
    response = await call_next(request)
    for k, v in SECURITY_HEADERS.items():
        if not (request.url.path == "/docs" and k == "Content-Security-Policy"):
            response.headers.setdefault(k, v)
    if request.url.path.startswith("/api/"):
        response.headers.setdefault("Cache-Control", "no-store")
    return response


@app.exception_handler(MetadataError)
@app.exception_handler(CortexError)
@app.exception_handler(PlanError)
async def friendly_error(_: Request, exc: Exception):
    return JSONResponse(status_code=400, content={"detail": str(exc)})


for r in (meta.router, sessions.router, connections.router, settings.router):
    app.include_router(r)


@app.get("/api/health")
def health():
    ok = check_db()
    return JSONResponse(status_code=200 if ok else 503, content={"ok": ok, "database": "ok" if ok else "unavailable"})


app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.get("/", include_in_schema=False)
def index():
    return FileResponse(STATIC_DIR / "index.html")
