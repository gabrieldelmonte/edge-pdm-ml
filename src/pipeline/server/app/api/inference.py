"""Inference endpoint consumed by the middleware pipeline."""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.domain.schemas import InferenceRequestPayload, InferenceResponsePayload
from app.ml.inference_engine import InferenceEngine
from app.services.inference_service import InferenceService

router = APIRouter()


def get_inference_engine() -> InferenceEngine:
    """Return the shared InferenceEngine instance from application state.

    Returns:
        The application-scoped InferenceEngine instance.
    """
    from app.main import inference_engine

    return inference_engine


@router.post(
    "/inference",
    response_model=InferenceResponsePayload,
    summary="Run middleware inference and return selected model result",
)
def infer(
    payload: InferenceRequestPayload,
    db: Session = Depends(get_db),
) -> InferenceResponsePayload:
    """Run all models for the CSV payload and return the selected model result.

    Spectral statistics (avg, std_dev) are computed from the normalized frequency
    spectrum magnitudes of the radial (y-axis) accelerometer channel. Both values
    are included in the response so the middleware can relay them alongside the
    inference label to the originating sensor via MQTT.

    Args:
        payload: Validated inference request from the middleware pipeline.
        db: Injected database session.

    Returns:
        InferenceResponsePayload containing the selected model result, spectral
        statistics, and all model results.
    """
    engine = get_inference_engine()
    return InferenceService(engine=engine, db=db).run(payload)
