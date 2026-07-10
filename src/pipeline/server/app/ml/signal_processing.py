"""Signal parsing and feature extraction for online inference.

Attempts to import extract_features and build_windows from the shared AI module
(PYTHONPATH=/app/ai-shared). Falls back to local implementations on ImportError.
"""

from __future__ import annotations

import math
from typing import Any

import numpy as np
from scipy import signal, stats

try:
    from ai.shared.feature_extraction import (  # type: ignore[import]
        build_windows as _build_windows_shared,
        extract_features as _extract_features_shared,
    )

    _USE_SHARED = True
except ImportError:
    _USE_SHARED = False


SOURCE_SAMPLE_RATE = 5_000
SAMPLE_RATE = 5_000
WINDOW_SIZE = 256
WINDOW_STRIDE = 256
N_CHANNELS = 3
FREQ_ANALYSIS_MAX_HZ = 5_000.0
FREQ_ANALYSIS_STEP_HZ = 250.0

CLASSES = [
    "normal",
    "horizontal-misalignment",
    "vertical-misalignment",
    "imbalance",
    "overhang-ball_fault",
    "overhang-cage_fault",
    "overhang-outer_race",
    "underhang-ball_fault",
    "underhang-cage_fault",
    "underhang-outer_race",
]


def parse_csv_matrix(csv_data: str) -> np.ndarray:
    """Parse CSV text into a float32 (n_rows, 3) accelerometer matrix.

    Args:
        csv_data: Raw CSV text with x,y,z columns per row.

    Raises:
        ValueError: When no parseable rows are found or a row has wrong column count.
    """
    rows: list[list[float]] = []
    for raw_line in csv_data.splitlines():
        line = raw_line.strip()
        if not line:
            continue

        parsed: list[float] = []
        for token in line.split(","):
            token = token.strip()
            if not token:
                continue
            try:
                parsed.append(float(token))
            except ValueError:
                parsed = []
                break

        if parsed:
            if len(parsed) != N_CHANNELS:
                raise ValueError(
                    f"CSV payload row has {len(parsed)} columns; expected exactly 3 (x,y,z)"
                )
            rows.append(parsed)

    if not rows:
        raise ValueError("CSV payload has no parseable numeric rows")

    matrix = np.zeros((len(rows), N_CHANNELS), dtype=np.float32)
    for row_idx, row in enumerate(rows):
        n_cols = min(len(row), N_CHANNELS)
        if n_cols:
            matrix[row_idx, :n_cols] = np.array(row[:n_cols], dtype=np.float32)
    return matrix


def _select_sensor_channels(matrix: np.ndarray) -> np.ndarray:
    """Validate matrix shape and return float32 copy."""
    if matrix.ndim != 2:
        raise ValueError("Sensor matrix must be 2-dimensional")
    if matrix.shape[1] != N_CHANNELS:
        raise ValueError(f"Sensor matrix has {matrix.shape[1]} columns; expected {N_CHANNELS}")
    return matrix.astype(np.float32, copy=False)


def _resample_channels(
    channels: np.ndarray,
    source_sample_rate: int = SOURCE_SAMPLE_RATE,
    target_sample_rate: int = SAMPLE_RATE,
) -> np.ndarray:
    """Resample channels with anti-alias filtering along the time axis."""
    if source_sample_rate <= 0 or target_sample_rate <= 0:
        raise ValueError("Sample rates must be positive")
    if source_sample_rate == target_sample_rate:
        return channels.astype(np.float32, copy=False)
    ratio_gcd = math.gcd(source_sample_rate, target_sample_rate)
    up = target_sample_rate // ratio_gcd
    down = source_sample_rate // ratio_gcd
    resampled = signal.resample_poly(channels, up=up, down=down, axis=0)
    return resampled.astype(np.float32, copy=False)


def _window_signal_local(channels: np.ndarray, stride: int = WINDOW_STRIDE) -> np.ndarray:
    """Create normalized windows from sensor rows (local fallback implementation)."""
    n_rows = channels.shape[0]
    if n_rows == 0:
        raise ValueError("CSV payload has zero rows")
    if stride <= 0:
        raise ValueError("Window stride must be greater than zero")

    windows: list[np.ndarray] = []
    if n_rows < WINDOW_SIZE:
        repeats = (WINDOW_SIZE + n_rows - 1) // n_rows
        padded = np.tile(channels, (repeats, 1))[:WINDOW_SIZE, :]
        windows.append(padded)
    else:
        for start in range(0, n_rows - WINDOW_SIZE + 1, stride):
            windows.append(channels[start : start + WINDOW_SIZE, :])

    window_batch = np.stack(windows, axis=0).astype(np.float32, copy=False)
    mean = np.mean(window_batch, axis=1, keepdims=True)
    std = np.std(window_batch, axis=1, keepdims=True) + 1e-8
    return (window_batch - mean) / std


def _extract_features_local(windowed_samples: np.ndarray) -> np.ndarray:
    """Extract handcrafted features using linspace band boundaries (matches training).

    Uses np.linspace for frequency band edges — identical to the canonical
    implementation in ai.shared.feature_extraction — so this fallback produces
    the same features as the shared module. This prevents silent mispredictions
    when the Docker shared-module import is unavailable.
    """
    n_samples, window_size, n_channels = windowed_samples.shape
    freqs = np.fft.rfftfreq(window_size, d=1.0 / SAMPLE_RATE)
    n_bands = 5
    band_edges = np.linspace(0, len(freqs), n_bands + 1, dtype=int)

    all_features: list[list[float]] = []
    for sample_idx in range(n_samples):
        sample_features: list[float] = []
        for channel_idx in range(n_channels):
            sig = windowed_samples[sample_idx, :, channel_idx].astype(np.float64)
            mean_val = float(np.mean(sig))
            std_val = float(np.std(sig))
            rms = float(np.sqrt(np.mean(sig**2)))
            peak = float(np.max(np.abs(sig)))
            ptp = float(np.ptp(sig))
            kurt = float(stats.kurtosis(sig))
            skew = float(stats.skew(sig))
            crest = peak / (rms + 1e-10)
            mean_abs = float(np.mean(np.abs(sig))) + 1e-10
            shape_factor = rms / mean_abs
            impulse = peak / mean_abs
            fft_mag = np.abs(np.fft.rfft(sig))
            power = fft_mag**2
            total_power = float(np.sum(power)) + 1e-10
            spectral_centroid = float(np.sum(freqs * power) / total_power)
            spectral_var = float(np.sum((freqs - spectral_centroid) ** 2 * power) / total_power)
            dominant_freq = float(freqs[int(np.argmax(fft_mag))])
            p_norm = power / total_power
            spectral_entropy = float(-np.sum(p_norm * np.log(p_norm + 1e-10)))
            band_energies = [
                float(np.sum(power[band_edges[k] : band_edges[k + 1]]) / total_power)
                for k in range(n_bands)
            ]
            sample_features.extend([
                mean_val, std_val, rms, peak, ptp, kurt, skew, crest,
                shape_factor, impulse, spectral_centroid, spectral_var,
                dominant_freq, spectral_entropy, *band_energies,
            ])
        all_features.append(sample_features)

    features = np.array(all_features, dtype=np.float32)
    feat_mean = np.mean(features, axis=1, keepdims=True)
    feat_std = np.std(features, axis=1, keepdims=True) + 1e-8
    return (features - feat_mean) / feat_std


def extract_features(windowed_samples: np.ndarray) -> np.ndarray:
    """Extract features, delegating to the shared AI module when available.

    Args:
        windowed_samples: Float32 array (n_samples, window_size, n_channels).
    """
    if _USE_SHARED:
        return _extract_features_shared(windowed_samples)  # type: ignore[no-any-return]
    return _extract_features_local(windowed_samples)


def build_model_inputs(
    csv_data: str,
    source_sample_rate: int | None = None,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Convert CSV text into (matrix, x_raw, x_feat) model-ready batches.

    Args:
        csv_data: Raw CSV text with x,y,z columns.
        source_sample_rate: Sensor Hz; None defaults to SOURCE_SAMPLE_RATE (no-op).
    """
    effective_source = source_sample_rate if source_sample_rate is not None else SOURCE_SAMPLE_RATE
    matrix = parse_csv_matrix(csv_data)
    channels = _select_sensor_channels(matrix)
    channels = _resample_channels(channels, source_sample_rate=effective_source)
    x_raw = _build_windows_shared(channels) if _USE_SHARED else _window_signal_local(channels)
    x_feat = extract_features(x_raw)
    return matrix, x_raw, x_feat


def _normalized_fft(sig: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Return (frequencies, normalized magnitudes) for a 1D signal."""
    centered = sig.astype(np.float64) - float(np.mean(sig))
    mags = np.abs(np.fft.rfft(centered))
    freqs = np.fft.rfftfreq(len(centered), d=1.0 / SAMPLE_RATE)
    max_mag = float(np.max(mags))
    if max_mag > 0:
        mags = mags / max_mag
    return freqs, mags


def _resample_spectrum(
    freqs: np.ndarray, mags: np.ndarray, target_freqs: np.ndarray
) -> np.ndarray:
    """Interpolate magnitude values onto a fixed frequency grid."""
    if len(freqs) == 0 or len(mags) == 0:
        return np.zeros_like(target_freqs, dtype=np.float64)
    return np.interp(target_freqs, freqs, mags, left=0.0, right=0.0)


def compute_frequency_analysis(
    matrix: np.ndarray,
    bearing_type: str = "underhang",
    source_sample_rate: int | None = None,
) -> dict[str, Any]:
    """Produce bearing-type-aware radial spectrum for dashboard visualization.

    Args:
        matrix: Raw sensor matrix from parse_csv_matrix.
        bearing_type: 'underhang' or 'overhang'.
        source_sample_rate: Sensor Hz; None defaults to SOURCE_SAMPLE_RATE.
    """
    if matrix.shape[0] < 2:
        return {
            "freq_hz": [], "underhang_mag": [], "overhang_mag": [],
            "message": "Not enough samples for frequency analysis",
        }

    effective_source = source_sample_rate if source_sample_rate is not None else SOURCE_SAMPLE_RATE
    channels = _select_sensor_channels(matrix)
    channels = _resample_channels(channels, source_sample_rate=effective_source)
    radial_signal = channels[:, 1]
    freqs, mags = _normalized_fft(radial_signal)

    mask = freqs <= FREQ_ANALYSIS_MAX_HZ
    freqs = freqs[mask]
    mags = mags[mask]

    if len(freqs) == 0:
        return {
            "freq_hz": [], "underhang_mag": [], "overhang_mag": [],
            "message": "No frequency bins available",
        }

    target_freqs = np.arange(
        0.0, FREQ_ANALYSIS_MAX_HZ + FREQ_ANALYSIS_STEP_HZ, FREQ_ANALYSIS_STEP_HZ, dtype=np.float64
    )
    bearing_mag = _resample_spectrum(freqs, mags, target_freqs)
    freq_list = [float(v) for v in target_freqs.tolist()]
    mag_list = [float(v) for v in bearing_mag.tolist()]

    if bearing_type == "overhang":
        return {"freq_hz": freq_list, "underhang_mag": [], "overhang_mag": mag_list, "message": "ok"}
    return {"freq_hz": freq_list, "underhang_mag": mag_list, "overhang_mag": [], "message": "ok"}
