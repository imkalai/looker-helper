import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from app.api import connections, meta, sessions, settings
from app.config import BASE_DIR
from app.db import check_db, init_db, reset_engine
from app.services.cortex.base import CortexError
from app.services.metadata.base import MetadataError
from app.services.planner import PlanError

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logging.getLogger("alembic.runtime.plugins").setLevel(logging.WARNING)
STATIC_DIR = BASE_DIR / "static"


@asynccontextmanager
async def lifespan(_: FastAPI):
    init_db()
    yield
    reset_engine()


app = FastAPI(title="Looker Helper", version="1.0.0", lifespan=lifespan)


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
