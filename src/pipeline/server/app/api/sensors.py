"""Sensor CRUD routes: list, create, delete, and update."""

from __future__ import annotations

from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.database import get_db
from app.core.exceptions import BadRequestError, ConflictError, NotFoundError, UnauthorizedError
from app.core.utils import normalize_bearing_type
from app.domain.orm import Sensor, User
from app.domain.repositories import (
    create_sensor,
    delete_sensor,
    get_owned_sensor,
    get_sensor_by_uid,
    get_sensors_for_user,
    get_user_by_id,
    update_sensor,
)
from app.domain.schemas import (
    ErrorResponse,
    SensorCreateRequest,
    SensorMutationResponse,
    SensorResponse,
    SensorUpdateRequest,
    StatusMessageResponse,
)
from app.services.sensor_service import CLAIMABLE_SENSOR_OWNERS

router = APIRouter()


def _require_user(request: Request, db: Session) -> User:
    """Return the authenticated user or raise 401."""
    user_id = request.session.get("user_id")
    if not user_id:
        raise UnauthorizedError("Login required")
    user = get_user_by_id(db, int(user_id))
    if user is None:
        raise UnauthorizedError("Login required")
    return user


def _connected(last_seen_at: datetime | None) -> bool:
    """Return True when the sensor was seen within the last 60 seconds."""
    if last_seen_at is None:
        return False
    return last_seen_at >= datetime.utcnow() - timedelta(minutes=1)


def _sensor_mutation_response(sensor: Sensor) -> SensorMutationResponse:
    """Build a SensorMutationResponse from a Sensor ORM instance."""
    return SensorMutationResponse(
        status="ok",
        sensor_uid=sensor.sensor_uid,
        bearing_type=sensor.bearing_type,
        selected_model=sensor.selected_model,
        active_accel=sensor.active_accel,
    )


@router.get(
    "",
    response_model=list[SensorResponse],
    summary="List sensors for logged-in user",
    responses={401: {"model": ErrorResponse}},
)
def list_sensors(request: Request, db: Session = Depends(get_db)) -> list[SensorResponse]:
    """List sensors owned by the logged-in user."""
    user = _require_user(request, db)
    return [
        SensorResponse(
            sensor_uid=s.sensor_uid,
            bearing_type=s.bearing_type,
            selected_model=s.selected_model,
            is_active=s.is_active,
            connected=_connected(s.last_seen_at),
            last_seen_at=s.last_seen_at,
            updated_at=s.updated_at,
            active_accel=s.active_accel,
        )
        for s in get_sensors_for_user(db, user.id)
    ]


@router.post(
    "",
    response_model=SensorMutationResponse,
    summary="Create a sensor",
    responses={400: {"model": ErrorResponse}, 401: {"model": ErrorResponse}, 409: {"model": ErrorResponse}},
)
def create_sensor_route(
    payload: SensorCreateRequest,
    request: Request,
    db: Session = Depends(get_db),
) -> SensorMutationResponse:
    """Create or claim a sensor for the logged-in user.

    When the sensor_uid belongs to a claimable service owner, the sensor is
    re-assigned to the current user instead of raising a conflict.
    """
    user = _require_user(request, db)
    sensor_uid = payload.sensor_uid.strip()
    if not sensor_uid:
        raise BadRequestError("sensor_uid is required")

    bearing_type = normalize_bearing_type(payload.bearing_type)
    selected_model = payload.selected_model.strip() or settings.default_sensor_model

    existing = get_sensor_by_uid(db, sensor_uid)
    if existing is not None:
        return _handle_existing_sensor(db, existing, user, bearing_type, selected_model)

    sensor = create_sensor(
        db, sensor_uid=sensor_uid, user_id=user.id,
        bearing_type=bearing_type, selected_model=selected_model,
    )
    return _sensor_mutation_response(sensor)


def _handle_existing_sensor(
    db: Session, existing: Sensor, user: User, bearing_type: str, selected_model: str
) -> SensorMutationResponse:
    """Claim or reject an existing sensor based on current owner.

    Raises:
        ConflictError: When the sensor cannot be claimed by this user.
    """
    if existing.user_id == user.id:
        raise ConflictError("sensor_uid already exists")

    owner = get_user_by_id(db, existing.user_id)
    if owner is not None and owner.username in CLAIMABLE_SENSOR_OWNERS:
        updated = update_sensor(
            db, existing,
            user_id=user.id, bearing_type=bearing_type,
            selected_model=selected_model, is_active=True,
        )
        return _sensor_mutation_response(updated)

    raise ConflictError("sensor_uid already exists")


@router.delete(
    "/{sensor_uid}",
    response_model=StatusMessageResponse,
    summary="Delete a sensor",
    responses={401: {"model": ErrorResponse}, 404: {"model": ErrorResponse}},
)
def delete_sensor_route(
    sensor_uid: str, request: Request, db: Session = Depends(get_db)
) -> StatusMessageResponse:
    """Delete one sensor owned by the logged-in user."""
    user = _require_user(request, db)
    sensor = get_owned_sensor(db, sensor_uid, user.id)
    if sensor is None:
        raise NotFoundError("Sensor not found")
    delete_sensor(db, sensor)
    return StatusMessageResponse(status="ok", message=f"Sensor {sensor_uid} deleted")


@router.patch(
    "/{sensor_uid}",
    response_model=SensorMutationResponse,
    summary="Update a sensor",
    responses={401: {"model": ErrorResponse}, 404: {"model": ErrorResponse}},
)
def update_sensor_route(
    sensor_uid: str,
    payload: SensorUpdateRequest,
    request: Request,
    db: Session = Depends(get_db),
) -> SensorMutationResponse:
    """Update sensor bearing type, selected model, or accelerometer preference."""
    user = _require_user(request, db)
    sensor = get_owned_sensor(db, sensor_uid, user.id)
    if sensor is None:
        raise NotFoundError("Sensor not found")

    updates: dict[str, object] = {}
    if payload.bearing_type is not None:
        updates["bearing_type"] = normalize_bearing_type(payload.bearing_type)
    if payload.selected_model is not None:
        model_name = payload.selected_model.strip()
        if model_name:
            updates["selected_model"] = model_name
    if payload.active_accel is not None:
        if payload.active_accel not in {"lis3dh", "adxl345"}:
            raise BadRequestError("active_accel must be 'lis3dh' or 'adxl345'")
        updates["active_accel"] = payload.active_accel

    updated = update_sensor(db, sensor, **updates)
    return _sensor_mutation_response(updated)
