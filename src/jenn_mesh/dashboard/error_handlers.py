"""Global exception handlers for the JennMesh dashboard."""

from __future__ import annotations

import logging

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

logger = logging.getLogger(__name__)


async def _handle_http_exception(request: Request, exc: HTTPException) -> JSONResponse:
    """Handle FastAPI HTTPException — log and return structured JSON."""
    if exc.status_code >= 500:
        logger.error("HTTP %d on %s: %s", exc.status_code, request.url.path, exc.detail)
    else:
        logger.warning("HTTP %d on %s: %s", exc.status_code, request.url.path, exc.detail)
    return JSONResponse(
        status_code=exc.status_code,
        content={"detail": exc.detail, "status_code": exc.status_code},
    )


async def _handle_validation_error(request: Request, exc: RequestValidationError) -> JSONResponse:
    """Handle Pydantic / path-parameter validation errors."""
    logger.warning("Validation error on %s: %s", request.url.path, exc.errors())
    return JSONResponse(
        status_code=422,
        content={
            "detail": "Validation error",
            "errors": exc.errors(),
        },
    )


async def _handle_unhandled_exception(request: Request, exc: Exception) -> JSONResponse:
    """Catch-all for unexpected server errors — log full traceback."""
    logger.exception("Unhandled error on %s %s", request.method, request.url.path)
    return JSONResponse(
        status_code=500,
        content={"detail": "Internal server error"},
    )


def register_error_handlers(app: FastAPI) -> None:
    """Register all global exception handlers on the app."""
    app.add_exception_handler(HTTPException, _handle_http_exception)
    app.add_exception_handler(RequestValidationError, _handle_validation_error)
    app.add_exception_handler(Exception, _handle_unhandled_exception)
