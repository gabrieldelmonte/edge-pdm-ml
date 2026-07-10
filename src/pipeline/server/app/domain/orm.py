"""SQLAlchemy ORM models for users, sensors, and inference records."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base


class User(Base):
    """Application user who owns one or more sensors."""

    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    username: Mapped[str] = mapped_column(String(128), unique=True, nullable=False, index=True)
    password_hash: Mapped[str] = mapped_column(String(256), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)

    sensors: Mapped[list[Sensor]] = relationship(back_populates="user", cascade="all, delete-orphan")


class Sensor(Base):
    """Physical or emulated sensor associated with a user account."""

    __tablename__ = "sensors"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    sensor_uid: Mapped[str] = mapped_column(String(128), unique=True, nullable=False, index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False, index=True)
    bearing_type: Mapped[str] = mapped_column(String(32), default="underhang", nullable=False)
    selected_model: Mapped[str] = mapped_column(String(64), default="LightGBM", nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    # Maps to the `inference_accel` DB column — controls which accelerometer's
    # data is displayed for both inference results and spectral analysis.
    # Values: 'lis3dh' | 'adxl345' | None (None = use the sensor's own type).
    active_accel: Mapped[str | None] = mapped_column("inference_accel", String(16), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
        nullable=False,
    )
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    user: Mapped[User] = relationship(back_populates="sensors")
    inference_records: Mapped[list[InferenceRecord]] = relationship(
        back_populates="sensor",
        cascade="all, delete-orphan",
    )


class InferenceRecord(Base):
    """Inference transaction stored for UI inspection and analytics."""

    __tablename__ = "inference_records"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    sensor_id: Mapped[int] = mapped_column(ForeignKey("sensors.id"), nullable=False, index=True)
    file_id: Mapped[str] = mapped_column(String(256), nullable=False, index=True)
    checksum: Mapped[str] = mapped_column(String(256), nullable=False)
    selected_model: Mapped[str] = mapped_column(String(64), nullable=False)
    selected_inference: Mapped[str] = mapped_column(String(128), nullable=False)
    model_results_json: Mapped[str] = mapped_column(Text, nullable=False)
    analysis_json: Mapped[str] = mapped_column(Text, nullable=False)
    csv_data: Mapped[str] = mapped_column(Text, nullable=False)
    current_ma: Mapped[float | None] = mapped_column(Float, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)

    sensor: Mapped[Sensor] = relationship(back_populates="inference_records")
