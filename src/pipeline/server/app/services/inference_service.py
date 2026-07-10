"""Inference pipeline service encapsulating the full request-to-response cycle."""

from __future__ import annotations

import hashlib
import json
from typing import Any

import numpy as np
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.utils import normalize_bearing_type
from app.domain.orm import Sensor
from app.domain.repositories import create_inference_record
from app.domain.schemas import FrequencyAnalysisResponse, InferenceRequestPayload, InferenceResponsePayload
from app.ml.inference_engine import MODEL_REGISTRY, InferenceEngine
from app.ml.signal_processing import build_model_inputs, compute_frequency_analysis
from app.services.sensor_service import resolve_sensor_for_inference


def _to_frequency_analysis(payload: dict[str, Any]) -> FrequencyAnalysisResponse:
    """Build FrequencyAnalysisResponse from a raw dict."""
    def to_floats(values: Any) -> list[float]:
        if not isinstance(values, list):
            return []
        result: list[float] = []
        for v in values:
            try:
                result.append(float(v))
            except (TypeError, ValueError):
                continue
        return result

    message = payload.get("message", "ok")
    return FrequencyAnalysisResponse(
        freq_hz=to_floats(payload.get("freq_hz", [])),
        underhang_mag=to_floats(payload.get("underhang_mag", [])),
        overhang_mag=to_floats(payload.get("overhang_mag", [])),
        message=message if isinstance(message, str) else str(message),
    )


def _build_error_results(error_message: str) -> dict[str, dict[str, Any]]:
    """Create an all-model error payload for consistent UI rendering."""
    return {
        spec.display_name: {
            "status": "error",
            "inference": "InferenceError",
            "confidence": 0.0,
            "class_index": -1,
            "error": error_message,
        }
        for spec in MODEL_REGISTRY
    }


class InferenceService:
    """Orchestrates the full inference pipeline from payload to persisted response."""

    def __init__(self, engine: InferenceEngine, db: Session) -> None:
        """Initialize with a shared inference engine and a DB session.

        Args:
            engine: Shared InferenceEngine instance with loaded model caches.
            db: Active SQLAlchemy session.
        """
        self._engine = engine
        self._db = db

    def run(self, payload: InferenceRequestPayload) -> InferenceResponsePayload:
        """Execute the full inference pipeline and return the response payload.

        Args:
            payload: Validated inference request from the middleware.
        """
        sensor = self._resolve_sensor(payload)
        checksum_valid = self._verify_checksum(payload)
        model_results, analysis, avg, std_dev = self._process(payload, sensor)
        selected_model, selected_result = self._engine.choose_result(
            model_results, sensor.selected_model
        )
        self._persist(payload, sensor, selected_model, selected_result, model_results, analysis, avg, std_dev)
        return self._build_response(
            payload, sensor, selected_model, selected_result, model_results, analysis, avg, std_dev, checksum_valid
        )

    def _resolve_sensor(self, payload: InferenceRequestPayload) -> Sensor:
        """Resolve or auto-create the sensor referenced by the request."""
        sensor_uid = (payload.sensor_id or settings.default_sensor_id).strip() or settings.default_sensor_id
        return resolve_sensor_for_inference(self._db, sensor_uid)

    def _verify_checksum(self, payload: InferenceRequestPayload) -> bool:
        """Return True when the computed SHA-256 matches the supplied checksum."""
        computed = hashlib.sha256(payload.csv_data.encode("utf-8")).hexdigest()
        return computed.lower() == payload.checksum.lower()

    def _process(
        self, payload: InferenceRequestPayload, sensor: Sensor
    ) -> tuple[dict[str, dict[str, Any]], FrequencyAnalysisResponse, float, float]:
        """Parse CSV, extract features, run all models, compute spectral stats."""
        try:
            matrix, x_raw, x_feat = build_model_inputs(
                payload.csv_data, source_sample_rate=payload.sample_rate_hz
            )
            freq_raw = compute_frequency_analysis(
                matrix, sensor.bearing_type, source_sample_rate=payload.sample_rate_hz
            )
            analysis = _to_frequency_analysis(freq_raw)
            avg, std_dev = self._compute_spectrum_stats(analysis, sensor.bearing_type)
            model_results = self._engine.predict_all(sensor.bearing_type, x_raw, x_feat)
        except Exception as exc:  # pragma: no cover - runtime fault tolerance
            analysis = FrequencyAnalysisResponse(
                freq_hz=[], underhang_mag=[], overhang_mag=[], message=str(exc)
            )
            model_results = _build_error_results(str(exc))
            avg, std_dev = 0.0, 0.0
        return model_results, analysis, avg, std_dev

    def _compute_spectrum_stats(
        self, analysis: FrequencyAnalysisResponse, bearing_type: str
    ) -> tuple[float, float]:
        """Return (mean, std_dev) of the radial frequency spectrum magnitudes."""
        mags = (
            analysis.overhang_mag
            if normalize_bearing_type(bearing_type) == "overhang"
            else analysis.underhang_mag
        )
        valid = [m for m in mags if m is not None]
        return (float(np.mean(valid)), float(np.std(valid))) if valid else (0.0, 0.0)

    def _persist(
        self, payload: InferenceRequestPayload, sensor: Sensor,
        selected_model: str, selected_result: dict[str, Any],
        model_results: dict[str, dict[str, Any]], analysis: FrequencyAnalysisResponse,
        avg: float, std_dev: float,
    ) -> None:
        """Persist the inference record to the database."""
        analysis_payload = analysis.model_dump()
        analysis_payload["bearing_type"] = normalize_bearing_type(sensor.bearing_type)
        analysis_payload["avg"] = avg
        analysis_payload["std_dev"] = std_dev
        create_inference_record(
            self._db,
            sensor_id=sensor.id,
            file_id=payload.file_id,
            checksum=payload.checksum,
            selected_model=selected_model,
            selected_inference=str(selected_result.get("inference", "InferenceUnavailable")),
            model_results_json=json.dumps(model_results),
            analysis_json=json.dumps(analysis_payload),
            csv_data=payload.csv_data,
            current_ma=payload.current_ma,
        )

    def _build_response(
        self, payload: InferenceRequestPayload, sensor: Sensor,
        selected_model: str, selected_result: dict[str, Any],
        model_results: dict[str, dict[str, Any]], analysis: FrequencyAnalysisResponse,
        avg: float, std_dev: float, checksum_valid: bool,
    ) -> InferenceResponsePayload:
        """Construct the InferenceResponsePayload from all computed artifacts."""
        return InferenceResponsePayload(
            status="ok" if selected_result.get("status") == "ok" else "error",
            file_id=payload.file_id,
            sensor_id=sensor.sensor_uid,
            bearing_type=sensor.bearing_type,
            selected_model=selected_model,
            inference=str(selected_result.get("inference", "InferenceUnavailable")),
            model=selected_model,
            checksum_valid=checksum_valid,
            avg=avg,
            std_dev=std_dev,
            current_ma=payload.current_ma,
            sample_rate_hz=payload.sample_rate_hz,
            all_model_results=model_results,
            analysis=analysis,
        )
