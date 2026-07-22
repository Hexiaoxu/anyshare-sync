"""AnyShare → BISHENG sync middleware — FastAPI application entry."""

import logging
import sys

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.admin import router as admin_router
from app.middleware import TraceMiddleware, TraceContextFilter
from app.models import init_db

# ── Logging setup ──────────────────────────────────────────
_trace_filter = TraceContextFilter()

def setup_logging():
    fmt = "%(asctime)s [%(levelname)s] [trace=%(trace_id)s] %(name)s: %(message)s"
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(logging.Formatter(fmt, datefmt="%Y-%m-%d %H:%M:%S"))
    handler.addFilter(_trace_filter)
    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(logging.INFO)


def create_app() -> FastAPI:
    setup_logging()

    app = FastAPI(
        title="AnyShare → BISHENG Sync Middleware",
        version="0.1.0",
        description="One-way sync from AnyShare doc libs to BISHENG knowledge spaces.",
    )

    app.add_middleware(TraceMiddleware)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.on_event("startup")
    def on_startup():
        init_db()

    app.include_router(admin_router)
    return app


app = create_app()


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
