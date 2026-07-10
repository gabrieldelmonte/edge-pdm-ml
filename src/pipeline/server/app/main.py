"""FastAPI application entry point."""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware

from app.api import auth, dashboard, inference
from app.api import models as models_router
from app.api import sensors
from app.core.config import settings
from app.core.database import Base, engine
from app.domain.schemas import ErrorResponse
from app.ml.inference_engine import InferenceEngine

OPENAPI_TAGS = [
    {"name": "System", "description": "Health checks and infrastructure status."},
    {"name": "Auth", "description": "Session-based authentication endpoints."},
    {"name": "Sensors", "description": "Sensor lifecycle and configuration endpoints."},
    {"name": "Models", "description": "Model discovery endpoints grouped by bearing type."},
    {
        "name": "Inference",
        "description": (
            "Online inference API consumed by the middleware pipeline. "
            "Accepts a raw CSV payload, verifies the SHA-256 checksum, runs all registered "
            "ML models, and returns the selected-model result with spectral statistics."
        ),
    },
    {"name": "Dashboard", "description": "Data retrieval APIs for the React frontend."},
]

app = FastAPI(
    title="Edge PdM Server API",
    version="0.2.0",
    description=(
        "Control-plane API for user registration, sensor management, model selection, "
        "and online inference for the Edge PdM pipeline."
    ),
    openapi_tags=OPENAPI_TAGS,
)

app.add_middleware(
    SessionMiddleware,
    secret_key=settings.secret_key,
    same_site="lax",
    https_only=False,
)

app.include_router(auth.router, prefix="/api/auth", tags=["Auth"])
app.include_router(sensors.router, prefix="/api/sensors", tags=["Sensors"])
app.include_router(models_router.router, prefix="/api/models", tags=["Models"])
app.include_router(inference.router, tags=["Inference"])
app.include_router(dashboard.router, prefix="/api", tags=["Dashboard"])

# Shared inference engine instance used by inference and models routers.
inference_engine = InferenceEngine(settings.model_root)

FRONTEND_DIST = Path(settings.frontend_dist)
FRONTEND_INDEX = FRONTEND_DIST / "index.html"
FRONTEND_ASSETS = FRONTEND_DIST / "assets"

if FRONTEND_ASSETS.exists():
    app.mount("/assets", StaticFiles(directory=str(FRONTEND_ASSETS)), name="assets")


def _serve_frontend() -> FileResponse | JSONResponse:
    """Return the React SPA entrypoint or a startup hint when the bundle is missing.

    Returns:
        FileResponse serving index.html, or JSONResponse with a 503 hint.
    """
    if FRONTEND_INDEX.exists():
        return FileResponse(
            FRONTEND_INDEX,
            headers={"Cache-Control": "no-store, max-age=0"},
        )
    return JSONResponse(
        status_code=503,
        content={
            "status": "error",
            "message": "Frontend bundle not found. Rebuild the server image to generate React assets.",
        },
    )


def _ensure_model_symlink() -> None:
    """Create a local models symlink inside the app container when possible."""
    source = Path("/models-source")
    target = Path(settings.model_root)
    if source.exists() and not target.exists():
        target.symlink_to(source, target_is_directory=True)


@app.on_event("startup")
def on_startup() -> None:
    """Initialize database schema and model symlink on application startup."""
    Base.metadata.create_all(bind=engine)
    _ensure_model_symlink()


@app.get("/", include_in_schema=False, response_model=None)
@app.get("/login", include_in_schema=False, response_model=None)
@app.get("/register", include_in_schema=False, response_model=None)
def frontend_entrypoint() -> FileResponse | JSONResponse:
    """Serve the React frontend for dashboard and authentication routes.

    Returns:
        FileResponse serving index.html when the bundle exists.
    """
    return _serve_frontend()


@app.get(
    "/health",
    tags=["System"],
    summary="Get service health",
)
def healthcheck() -> dict[str, str]:
    """Return a basic health status for orchestration checks.

    Returns:
        Dict with status key set to 'ok'.
    """
    return {"status": "ok"}


@app.exception_handler(Exception)
async def http_exception_handler(_request: Request, exc: Exception) -> JSONResponse:
    """Return JSON errors for all HTTP exceptions.

    Args:
        _request: The incoming request (unused).
        exc: The raised exception.

    Returns:
        JSONResponse with the error payload.
    """
    from fastapi import HTTPException

    if isinstance(exc, HTTPException):
        message = exc.detail if isinstance(exc.detail, str) else str(exc.detail)
        payload = ErrorResponse(message=message)
        return JSONResponse(status_code=exc.status_code, content=payload.model_dump())
    return JSONResponse(
        status_code=500,
        content={"status": "error", "message": "Internal server error"},
    )


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(
    _request: Request, exc: RequestValidationError
) -> JSONResponse:
    """Return a consistent payload for request validation errors.

    Args:
        _request: The incoming request (unused).
        exc: The validation error containing detailed field errors.

    Returns:
        JSONResponse with a 422 status and structured error details.
    """
    return JSONResponse(
        status_code=422,
        content={
            "status": "error",
            "message": "Validation error",
            "details": exc.errors(),
        },
    )
