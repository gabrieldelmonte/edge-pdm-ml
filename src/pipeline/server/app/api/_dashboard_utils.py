"""Shared helper utilities for dashboard route handlers."""

from __future__ import annotations

import json
from typing import Any

from fastapi.responses import Response

from app.core.exceptions import UnauthorizedError
from app.core.utils import normalize_bearing_type
from app.domain.orm import InferenceRecord
from app.domain.schemas import FrequencyAnalysisResponse

from fastapi import Request


def require_user_id(request: Request) -> int:
    """Return authenticated user ID or raise 401."""
    user_id = request.session.get("user_id")
    if not user_id:
        raise UnauthorizedError("Login required")
    return int(user_id)


def safe_json_loads(payload: str) -> dict[str, Any]:
    """Parse JSON to dict; return empty dict on failure."""
    try:
        parsed = json.loads(payload)
        return parsed if isinstance(parsed, dict) else {}
    except Exception:
        return {}


def to_float_list(values: Any) -> list[float]:
    """Coerce list elements to float, skipping non-numeric entries."""
    if not isinstance(values, list):
        return []
    result: list[float] = []
    for value in values:
        try:
            result.append(float(value))
        except (TypeError, ValueError):
            continue
    return result


def infer_bearing_type_from_analysis(
    payload: dict[str, Any], fallback: str | None = None
) -> str:
    """Infer bearing type from analysis_json payload for immutable history rows."""
    explicit = payload.get("bearing_type")
    if isinstance(explicit, str) and explicit.strip():
        return normalize_bearing_type(explicit)

    underhang_mag = to_float_list(payload.get("underhang_mag", []))
    overhang_mag = to_float_list(payload.get("overhang_mag", []))

    if underhang_mag and not overhang_mag:
        return "underhang"
    if overhang_mag and not underhang_mag:
        return "overhang"
    if underhang_mag and overhang_mag:
        under_e = sum(abs(v) for v in underhang_mag)
        over_e = sum(abs(v) for v in overhang_mag)
        return "overhang" if over_e > under_e else "underhang"
    return normalize_bearing_type(fallback)


def to_frequency_analysis(payload: dict[str, Any]) -> FrequencyAnalysisResponse:
    """Build FrequencyAnalysisResponse from a raw analysis_json dict."""
    message = payload.get("message", "ok")
    return FrequencyAnalysisResponse(
        freq_hz=to_float_list(payload.get("freq_hz", [])),
        underhang_mag=to_float_list(payload.get("underhang_mag", [])),
        overhang_mag=to_float_list(payload.get("overhang_mag", [])),
        message=message if isinstance(message, str) else str(message),
    )


def infer_record_accel(file_id: str | None) -> str | None:
    """Return 'lis3dh', 'adxl345', or None based on file_id naming."""
    normalized = (file_id or "").lower()
    if "lis3dh" in normalized:
        return "lis3dh"
    if "adxl345" in normalized:
        return "adxl345"
    return None


def csv_download_response(record: InferenceRecord) -> Response:
    """Build a CSV attachment Response from a persisted InferenceRecord."""
    safe_filename = record.file_id.replace("/", "_").replace("\\", "_") + ".csv"
    return Response(
        content=record.csv_data,
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{safe_filename}"'},
    )


def extract_active_spectrum(analysis_payload: dict[str, Any]) -> list[float]:
    """Return the populated magnitude array (underhang or overhang) from a payload."""
    bearing = analysis_payload.get("bearing_type", "underhang")
    primary_key = "overhang_mag" if bearing == "overhang" else "underhang_mag"
    fallback_key = "underhang_mag" if bearing == "overhang" else "overhang_mag"
    mags = to_float_list(analysis_payload.get(primary_key, []))
    return mags or to_float_list(analysis_payload.get(fallback_key, []))
