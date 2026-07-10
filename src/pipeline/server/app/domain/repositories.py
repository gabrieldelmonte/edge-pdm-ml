"""Typed repository functions wrapping all database query logic."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import desc, select
from sqlalchemy.orm import Session

from app.domain.orm import InferenceRecord, Sensor, User


# ---------------------------------------------------------------------------
# User repository
# ---------------------------------------------------------------------------


def get_user_by_username(db: Session, username: str) -> User | None:
    """Fetch a user by exact username match."""
    return db.execute(select(User).where(User.username == username)).scalar_one_or_none()


def get_user_by_id(db: Session, user_id: int) -> User | None:
    """Fetch a user by primary key."""
    return db.get(User, user_id)


def create_user(db: Session, *, username: str, password_hash: str) -> User:
    """Persist a new user and return the created instance.

    Args:
        db: Active SQLAlchemy session.
        username: Unique username.
        password_hash: Pre-hashed password string.
    """
    user = User(username=username, password_hash=password_hash)
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


# ---------------------------------------------------------------------------
# Sensor repository
# ---------------------------------------------------------------------------


def get_sensor_by_uid(db: Session, uid: str) -> Sensor | None:
    """Fetch a sensor by its unique sensor_uid."""
    return db.execute(select(Sensor).where(Sensor.sensor_uid == uid)).scalar_one_or_none()


def get_sensors_for_user(db: Session, user_id: int) -> list[Sensor]:
    """Return all sensors owned by user_id, ordered by sensor_uid."""
    return list(
        db.execute(
            select(Sensor).where(Sensor.user_id == user_id).order_by(Sensor.sensor_uid.asc())
        ).scalars()
    )


def get_owned_sensor(db: Session, uid: str, user_id: int) -> Sensor | None:
    """Fetch a sensor by sensor_uid, scoped to the owning user_id, or None."""
    return db.execute(
        select(Sensor).where(Sensor.sensor_uid == uid, Sensor.user_id == user_id)
    ).scalar_one_or_none()


def create_sensor(
    db: Session,
    *,
    sensor_uid: str,
    user_id: int,
    bearing_type: str,
    selected_model: str,
) -> Sensor:
    """Persist a new sensor and return the created instance.

    Args:
        db: Active SQLAlchemy session.
        sensor_uid: Unique sensor identifier.
        user_id: Owner user primary key.
        bearing_type: 'underhang' or 'overhang'.
        selected_model: Default model name.
    """
    sensor = Sensor(
        sensor_uid=sensor_uid,
        user_id=user_id,
        bearing_type=bearing_type,
        selected_model=selected_model,
        is_active=True,
    )
    db.add(sensor)
    db.commit()
    db.refresh(sensor)
    return sensor


def update_sensor(db: Session, sensor: Sensor, **fields: object) -> Sensor:
    """Apply field updates to a sensor and commit.

    Args:
        db: Active SQLAlchemy session.
        sensor: Sensor ORM instance to update.
        **fields: Column name to new value mappings.
    """
    for key, value in fields.items():
        setattr(sensor, key, value)
    db.commit()
    db.refresh(sensor)
    return sensor


def delete_sensor(db: Session, sensor: Sensor) -> None:
    """Delete a sensor record from the database."""
    db.delete(sensor)
    db.commit()


def touch_sensor(db: Session, sensor: Sensor) -> Sensor:
    """Update last_seen_at and mark sensor as active."""
    sensor.last_seen_at = datetime.utcnow()
    sensor.is_active = True
    db.commit()
    db.refresh(sensor)
    return sensor


# ---------------------------------------------------------------------------
# InferenceRecord repository
# ---------------------------------------------------------------------------


def create_inference_record(
    db: Session,
    *,
    sensor_id: int,
    file_id: str,
    checksum: str,
    selected_model: str,
    selected_inference: str,
    model_results_json: str,
    analysis_json: str,
    csv_data: str,
    current_ma: float | None,
) -> InferenceRecord:
    """Persist a new inference record and return the created instance.

    Args:
        db: Active SQLAlchemy session.
        sensor_id: FK referencing the owning sensor.
        file_id: Unique file identifier from the sensor transmission.
        checksum: SHA-256 hex digest of the CSV payload.
        selected_model: Name of the model whose result was selected.
        selected_inference: Class label from the selected model.
        model_results_json: JSON-encoded results for all models.
        analysis_json: JSON-encoded frequency analysis payload.
        csv_data: Raw CSV text from the sensor.
        current_ma: INA219 current reading in milliamps, or None.
    """
    record = InferenceRecord(
        sensor_id=sensor_id,
        file_id=file_id,
        checksum=checksum,
        selected_model=selected_model,
        selected_inference=selected_inference,
        model_results_json=model_results_json,
        analysis_json=analysis_json,
        csv_data=csv_data,
        current_ma=current_ma,
    )
    db.add(record)
    db.commit()
    db.refresh(record)
    return record


def get_latest_inference(
    db: Session,
    sensor_uid: str | None,
    limit: int,
    user_id: int,
) -> list[tuple[InferenceRecord, Sensor]]:
    """Return recent inference records owned by user_id.

    Args:
        db: Active SQLAlchemy session.
        sensor_uid: Optional filter to one sensor.
        limit: Maximum rows to return.
        user_id: Owner user PK for authorization scoping.
    """
    stmt = (
        select(InferenceRecord, Sensor)
        .join(Sensor, InferenceRecord.sensor_id == Sensor.id)
        .where(Sensor.user_id == user_id)
        .order_by(desc(InferenceRecord.created_at))
        .limit(limit)
    )
    if sensor_uid:
        stmt = stmt.where(Sensor.sensor_uid == sensor_uid)
    return list(db.execute(stmt).all())


def get_inference_record(db: Session, record_id: int) -> InferenceRecord | None:
    """Fetch a single inference record by primary key."""
    return db.get(InferenceRecord, record_id)


def get_owned_inference_record(
    db: Session, record_id: int, user_id: int
) -> InferenceRecord | None:
    """Fetch an inference record owned by user_id, or None when absent/unauthorized."""
    return db.execute(
        select(InferenceRecord)
        .join(Sensor, InferenceRecord.sensor_id == Sensor.id)
        .where(InferenceRecord.id == record_id, Sensor.user_id == user_id)
    ).scalar_one_or_none()


def get_latest_inference_for_sensor(
    db: Session, sensor_uid: str, user_id: int
) -> InferenceRecord | None:
    """Return the single most recent inference record for one sensor."""
    return db.execute(
        select(InferenceRecord)
        .join(Sensor, InferenceRecord.sensor_id == Sensor.id)
        .where(Sensor.user_id == user_id, Sensor.sensor_uid == sensor_uid)
        .order_by(desc(InferenceRecord.created_at))
        .limit(1)
    ).scalar_one_or_none()
