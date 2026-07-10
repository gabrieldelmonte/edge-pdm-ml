"""Model discovery routes grouped by bearing type."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.exceptions import UnauthorizedError
from app.core.utils import normalize_bearing_type
from app.domain.repositories import get_user_by_id
from app.domain.schemas import ErrorResponse, ModelListResponse
from app.ml.inference_engine import InferenceEngine

router = APIRouter()


def _get_user_id(request: Request) -> int:
    """Extract user ID from session or raise 401.

    Args:
        request: Incoming HTTP request with session cookie.

    Returns:
        Integer user_id from the session.

    Raises:
        UnauthorizedError: When no authenticated session exists.
    """
    user_id = request.session.get("user_id")
    if not user_id:
        raise UnauthorizedError("Login required")
    return int(user_id)


def get_inference_engine() -> InferenceEngine:
    """Return the shared InferenceEngine instance from application state.

    Returns:
        The application-scoped InferenceEngine instance.
    """
    from app.main import inference_engine

    return inference_engine


@router.get(
    "/{bearing_type}",
    response_model=ModelListResponse,
    summary="List model availability by bearing type",
    responses={401: {"model": ErrorResponse}},
)
def list_models(
    bearing_type: str,
    request: Request,
    db: Session = Depends(get_db),
) -> ModelListResponse:
    """Return model availability for a selected bearing type.

    Args:
        bearing_type: URL path parameter; 'underhang' or 'overhang'.
        request: HTTP request with session cookie for authentication.
        db: Injected database session.

    Returns:
        ModelListResponse with model availability for the bearing type.
    """
    user_id = _get_user_id(request)
    user = get_user_by_id(db, user_id)
    if user is None:
        raise UnauthorizedError("Login required")

    normalized = normalize_bearing_type(bearing_type)
    engine = get_inference_engine()
    return ModelListResponse(
        bearing_type=normalized,
        models=engine.list_models(normalized),
    )
