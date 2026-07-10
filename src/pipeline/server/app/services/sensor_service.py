"""Business logic for sensor resolution and auto-creation during inference."""

from __future__ import annotations

import re
import secrets
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.security import hash_password
from app.domain.orm import Sensor, User

SERVICE_USERNAME = "service.middleware"
CLAIMABLE_SENSOR_OWNERS = {SERVICE_USERNAME, "admin"}


def _base_uid_from_accel_sensor_uid(sensor_uid: str) -> str:
    """Collapse accelerometer suffix tokens to recover a base sensor UID.

    Args:
        sensor_uid: Possibly suffixed UID such as 'esp32-lis3dh' or 'esp32-adxl345'.

    Returns:
        Base UID with the accelerometer suffix stripped, or the original UID.
    """
    base_uid = re.sub(r"-(lis3dh|adxl345)\b", "", sensor_uid, flags=re.IGNORECASE)
    return base_uid.strip("-")


def _ensure_service_user(db: Session) -> User:
    """Ensure a non-human service user exists for middleware-created sensors.

    Args:
        db: Active SQLAlchemy session.

    Returns:
        The existing or newly created service user.
    """
    user = db.execute(select(User).where(User.username == SERVICE_USERNAME)).scalar_one_or_none()
    if user is not None:
        return user

    user = User(
        username=SERVICE_USERNAME,
        password_hash=hash_password(secrets.token_urlsafe(32)),
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def resolve_sensor_for_inference(db: Session, sensor_uid: str) -> Sensor:
    """Resolve or create the sensor record used by middleware inference calls.

    Precedence order:
    1. If the UID has an accel suffix and a base UID exists, use the base sensor.
    2. If an exact match exists, use it.
    3. Otherwise create a new sensor under the service user.

    Args:
        db: Active SQLAlchemy session.
        sensor_uid: UID from the inference request payload.

    Returns:
        The resolved or newly created Sensor instance.
    """
    base_uid = _base_uid_from_accel_sensor_uid(sensor_uid)
    if base_uid and base_uid != sensor_uid:
        base_sensor = db.execute(
            select(Sensor).where(Sensor.sensor_uid == base_uid)
        ).scalar_one_or_none()
        if base_sensor is not None:
            base_sensor.last_seen_at = datetime.utcnow()
            base_sensor.is_active = True
            db.commit()
            db.refresh(base_sensor)
            return base_sensor

    sensor = db.execute(
        select(Sensor).where(Sensor.sensor_uid == sensor_uid)
    ).scalar_one_or_none()
    if sensor is not None:
        sensor.last_seen_at = datetime.utcnow()
        sensor.is_active = True
        db.commit()
        db.refresh(sensor)
        return sensor

    user = _ensure_service_user(db)
    sensor = Sensor(
        sensor_uid=sensor_uid,
        user_id=user.id,
        bearing_type=settings.default_sensor_bearing,
        selected_model=settings.default_sensor_model,
        is_active=True,
        last_seen_at=datetime.utcnow(),
    )
    db.add(sensor)
    db.commit()
    db.refresh(sensor)
    return sensor
