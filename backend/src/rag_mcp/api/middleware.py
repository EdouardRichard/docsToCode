"""Request context middleware for request-level isolation (FR-023).

Generates a unique request_id per incoming request and propagates it via
contextvars so that all downstream code (services, LangGraph runs, logging)
can access it without explicit parameter passing. Ensures no shared mutable
state across concurrent requests.
"""

import uuid
from contextvars import ContextVar
from typing import Callable

from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import Response

# Request-scoped context variables
request_id_var: ContextVar[str] = ContextVar("request_id", default="")


def get_request_id() -> str:
    """Get the current request ID from context."""
    return request_id_var.get()


class RequestContextMiddleware(BaseHTTPMiddleware):
    """Middleware that assigns a unique request_id to each request.

    - Generates UUID4 request_id for every incoming request
    - Sets it in contextvars for downstream access
    - Adds X-Request-ID header to response for traceability
    - No shared mutable state between concurrent requests
    """

    async def dispatch(
        self, request: Request, call_next: RequestResponseEndpoint
    ) -> Response:
        req_id = str(uuid.uuid4())
        token = request_id_var.set(req_id)
        try:
            response = await call_next(request)
            response.headers["X-Request-ID"] = req_id
            return response
        finally:
            request_id_var.reset(token)
