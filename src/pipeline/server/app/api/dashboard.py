"""Dashboard routes: inference history, frequency analysis, and CSV downloads."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import Response
from sqlalchemy.orm import Session

from app.api._dashboard_utils import (
    csv_download_response,
    extract_active_spectrum,
    infer_bearing_type_from_analysis,
    infer_record_accel,
    require_user_id,
    safe_json_loads,
    to_float_list,
    to_frequency_analysis,
)
from app.core.database import get_db
from app.core.exceptions import ConflictError, NotFoundError
from app.domain.repositories import (
    get_latest_inference,
    get_latest_inference_for_sensor,
    get_owned_inference_record,
)
from app.domain.schemas import (
    ErrorResponse,
    FrequencyAnalysisResponse,
    InferenceLatestRow,
)

router = APIRouter()


@router.get(
    "/inference/latest",
    response_model=list[InferenceLatestRow],
    summary="Get latest inference rows",
    responses={401: {"model": ErrorResponse}},
)
def latest_inference(
    request: Request,
    sensor_uid: str | None = Query(default=None),
    limit: int = Query(default=25, ge=1, le=200),
    db: Session = Depends(get_db),
) -> list[InferenceLatestRow]:
    """Return recent inference rows for dashboard tables and details."""
    user_id = require_user_id(request)
    rows = get_latest_inference(db, sensor_uid, limit, user_id)
    result: list[InferenceLatestRow] = []

    for record, sensor in rows:
        analysis_payload = safe_json_loads(record.analysis_json)
        result.append(
            InferenceLatestRow(
                record_id=record.id,
                sensor_uid=sensor.sensor_uid,
                bearing_type=infer_bearing_type_from_analysis(
                    analysis_payload, fallback=sensor.bearing_type
                ),
                file_id=record.file_id,
                selected_model=record.selected_model,
                selected_inference=record.selected_inference,
                created_at=record.created_at,
                avg=analysis_payload.get("avg"),
                std_dev=analysis_payload.get("std_dev"),
                current_ma=record.current_ma,
                model_results=safe_json_loads(record.model_results_json),
            )
        )

    return result


@router.get(
    "/analysis/dual",
    response_model=FrequencyAnalysisResponse,
    summary="Get combined frequency analysis for LIS3DH and ADXL345",
    responses={401: {"model": ErrorResponse}},
)
def analysis_dual(
    request: Request,
    lis3dh_uid: str = Query(..., description="Sensor UID of the LIS3DH sensor"),
    adxl345_uid: str = Query(..., description="Sensor UID of the ADXL345 sensor"),
    db: Session = Depends(get_db),
) -> FrequencyAnalysisResponse:
    """Return latest frequency spectra for LIS3DH and ADXL345.

    underhang_mag carries LIS3DH spectrum; overhang_mag carries ADXL345.
    """
    user_id = require_user_id(request)

    lis3dh_row = get_latest_inference_for_sensor(db, lis3dh_uid, user_id)
    adxl345_row = get_latest_inference_for_sensor(db, adxl345_uid, user_id)

    lis3dh_payload = safe_json_loads(lis3dh_row.analysis_json) if lis3dh_row else None
    adxl345_payload = safe_json_loads(adxl345_row.analysis_json) if adxl345_row else None

    freq_hz = to_float_list((lis3dh_payload or adxl345_payload or {}).get("freq_hz", []))
    lis3dh_mag = extract_active_spectrum(lis3dh_payload) if lis3dh_payload else []
    adxl345_mag = extract_active_spectrum(adxl345_payload) if adxl345_payload else []

    if not lis3dh_mag and not adxl345_mag:
        message = "No inference records found for either sensor yet"
    elif not lis3dh_mag:
        message = f"No data for {lis3dh_uid} yet"
    elif not adxl345_mag:
        message = f"No data for {adxl345_uid} yet"
    else:
        message = "ok"

    return FrequencyAnalysisResponse(
        freq_hz=freq_hz, underhang_mag=lis3dh_mag, overhang_mag=adxl345_mag, message=message
    )


@router.get(
    "/analysis/{sensor_uid}",
    response_model=FrequencyAnalysisResponse,
    summary="Get latest frequency analysis for one sensor",
    responses={401: {"model": ErrorResponse}, 404: {"model": ErrorResponse}},
)
def analysis_single(
    sensor_uid: str,
    request: Request,
    db: Session = Depends(get_db),
) -> FrequencyAnalysisResponse:
    """Return the latest frequency analysis payload for one sensor."""
    user_id = require_user_id(request)
    row = get_latest_inference_for_sensor(db, sensor_uid, user_id)
    if row is None:
        raise NotFoundError("No inference record found for sensor")
    return to_frequency_analysis(safe_json_loads(row.analysis_json))


@router.get(
    "/inference/{record_id}/csv",
    summary="Download CSV payload for an inference record",
    responses={
        200: {"description": "CSV file attachment.", "content": {"text/csv": {}}},
        401: {"model": ErrorResponse},
        404: {"model": ErrorResponse},
    },
    response_class=Response,
)
def download_csv(
    record_id: int, request: Request, db: Session = Depends(get_db)
) -> Response:
    """Download raw CSV stored for a specific inference record."""
    user_id = require_user_id(request)
    record = get_owned_inference_record(db, record_id, user_id)
    if record is None:
        raise NotFoundError("Inference record not found")
    return csv_download_response(record)


@router.get(
    "/inference/{record_id}/csv/lis3dh",
    summary="Download CSV payload for a LIS3DH inference record",
    responses={
        200: {"description": "CSV attachment for LIS3DH data.", "content": {"text/csv": {}}},
        401: {"model": ErrorResponse},
        404: {"model": ErrorResponse},
        409: {"model": ErrorResponse},
    },
    response_class=Response,
)
def download_csv_lis3dh(
    record_id: int, request: Request, db: Session = Depends(get_db)
) -> Response:
    """Download CSV for a record explicitly identified as LIS3DH."""
    user_id = require_user_id(request)
    record = get_owned_inference_record(db, record_id, user_id)
    if record is None:
        raise NotFoundError("Inference record not found")
    accel = infer_record_accel(record.file_id)
    if accel != "lis3dh":
        raise ConflictError(f"record accel mismatch: expected lis3dh, got {accel or 'unknown'}")
    return csv_download_response(record)


@router.get(
    "/inference/{record_id}/csv/adxl345",
    summary="Download CSV payload for an ADXL345 inference record",
    responses={
        200: {"description": "CSV attachment for ADXL345 data.", "content": {"text/csv": {}}},
        401: {"model": ErrorResponse},
        404: {"model": ErrorResponse},
        409: {"model": ErrorResponse},
    },
    response_class=Response,
)
def download_csv_adxl345(
    record_id: int, request: Request, db: Session = Depends(get_db)
) -> Response:
    """Download CSV for a record explicitly identified as ADXL345."""
    user_id = require_user_id(request)
    record = get_owned_inference_record(db, record_id, user_id)
    if record is None:
        raise NotFoundError("Inference record not found")
    accel = infer_record_accel(record.file_id)
    if accel != "adxl345":
        raise ConflictError(f"record accel mismatch: expected adxl345, got {accel or 'unknown'}")
    return csv_download_response(record)
