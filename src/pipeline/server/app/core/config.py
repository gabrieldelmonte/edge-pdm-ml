"""Application settings loaded from environment variables."""

from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache


@dataclass(frozen=True)
class Settings:
    """Runtime configuration loaded from environment variables.

    Attributes:
        database_url: SQLAlchemy-compatible database connection string.
        secret_key: Secret used to sign session cookies.
        default_sensor_id: Fallback sensor UID when none is supplied in requests.
        default_sensor_bearing: Default bearing type for auto-created sensors.
        default_sensor_model: Default selected model for auto-created sensors.
        model_root: Filesystem path to the directory containing model artifacts.
        frontend_dist: Filesystem path to the compiled React frontend bundle.
    """

    database_url: str
    secret_key: str
    default_sensor_id: str
    default_sensor_bearing: str
    default_sensor_model: str
    model_root: str
    frontend_dist: str


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Load and return application settings from environment variables.

    Returns:
        A frozen Settings instance populated from the process environment.
    """
    default_bearing = os.getenv("APP_DEFAULT_SENSOR_BEARING", "underhang").strip().lower()
    if default_bearing not in {"underhang", "overhang"}:
        default_bearing = "underhang"

    return Settings(
        database_url=os.getenv(
            "DATABASE_URL",
            "postgresql+psycopg2://edge:edge@db:5432/edge_pdm",
        ),
        secret_key=os.getenv("APP_SECRET_KEY", "change-me-in-production"),
        default_sensor_id=os.getenv("APP_DEFAULT_SENSOR_ID", "mock-esp32-01"),
        default_sensor_bearing=default_bearing,
        default_sensor_model=os.getenv("APP_DEFAULT_SENSOR_MODEL", "LightGBM"),
        model_root=os.getenv("MODEL_ROOT", "/app/models"),
        frontend_dist=os.getenv("FRONTEND_DIST", "/app/frontend-dist"),
    )


settings: Settings = get_settings()
