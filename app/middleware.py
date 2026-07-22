"""Trace ID middleware — injects trace_id into every request and log record."""

import logging
import uuid

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request


TRACE_ID_HEADER = "X-Trace-Id"


class TraceContextFilter(logging.Filter):
    """Inject trace_id into log records."""

    def filter(self, record):
        import contextvars
        trace_id = contextvars.ContextVar("trace_id", default="").get()
        record.trace_id = trace_id or "-"
        return True


class TraceMiddleware(BaseHTTPMiddleware):
    """Extract or generate trace_id per request."""

    async def dispatch(self, request: Request, call_next):
        import contextvars

        trace_id = request.headers.get(TRACE_ID_HEADER, uuid.uuid4().hex[:12])
        cv = contextvars.ContextVar("trace_id", default="")
        cv.set(trace_id)

        response = await call_next(request)
        response.headers[TRACE_ID_HEADER] = trace_id
        return response
