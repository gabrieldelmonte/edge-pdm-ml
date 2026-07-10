from __future__ import annotations

import math

import numpy as np
from scipy import stats
from scipy.signal import resample_poly

from .constants import N_FREQ_BANDS, SAMPLE_RATE, WINDOW_SIZE, WINDOW_STRIDE


def resample_channels(
    channels: np.ndarray,
    source_sample_rate: int,
    target_sample_rate: int = SAMPLE_RATE,
) -> np.ndarray:
    """Resample multi-channel signal using polyphase filtering.

    Args:
        channels: Input array of shape (n_samples, n_channels).
        source_sample_rate: Original sample rate in Hz.
        target_sample_rate: Target sample rate in Hz.

    Returns:
        Resampled array of shape (new_n_samples, n_channels).
    """
    g = math.gcd(source_sample_rate, target_sample_rate)
    up = target_sample_rate // g
    down = source_sample_rate // g
    resampled = resample_poly(channels, up, down, axis=0)
    return resampled.astype(np.float32)


def build_windows(
    channels: np.ndarray,
    window_size: int = WINDOW_SIZE,
    stride: int = WINDOW_STRIDE,
) -> np.ndarray:
    """Slice channels into fixed-size windows with optional overlap.

    Args:
        channels: Array of shape (n_samples, n_channels).
        window_size: Number of samples per window.
        stride: Step between window start positions.

    Returns:
        Array of shape (n_windows, window_size, n_channels), float32, normalized per window.
    """
    n_samples, n_ch = channels.shape
    if n_samples < window_size:
        repeats = (window_size // n_samples) + 1
        channels = np.tile(channels, (repeats, 1))[:window_size]
        n_samples = window_size

    starts = range(0, n_samples - window_size + 1, stride)
    windows = np.stack([channels[s : s + window_size] for s in starts], axis=0).astype(
        np.float32
    )

    mean = windows.mean(axis=1, keepdims=True)
    std = windows.std(axis=1, keepdims=True) + 1e-8
    return (windows - mean) / std


def extract_features(windowed: np.ndarray, sample_rate: int = SAMPLE_RATE) -> np.ndarray:
    """Extract 19 time- and frequency-domain features per channel per window.

    Uses np.linspace for band energy binning to guarantee all FFT bins are covered.
    With N_FREQ_BANDS=5 and 3 channels this produces 57 features total; with 4 channels
    it produces 76 features total.

    Args:
        windowed: Array of shape (n_windows, window_size, n_channels).
        sample_rate: Sample rate used for frequency calculations.

    Returns:
        Normalized feature array of shape (n_windows, n_channels * 19), float32.
    """
    n_samples, win_size, n_ch = windowed.shape
    freqs = np.fft.rfftfreq(win_size, d=1.0 / sample_rate)
    band_edges = np.linspace(0, len(freqs), N_FREQ_BANDS + 1, dtype=int)

    all_features: list[list[float]] = []
    for sample_idx in range(n_samples):
        sample_features: list[float] = []
        for ch_idx in range(n_ch):
            sig = windowed[sample_idx, :, ch_idx].astype(np.float64)

            mean_val = float(np.mean(sig))
            std_val = float(np.std(sig))
            rms = float(np.sqrt(np.mean(sig**2)))
            peak = float(np.max(np.abs(sig)))
            ptp = float(np.ptp(sig))
            kurt = float(stats.kurtosis(sig))
            skew = float(stats.skew(sig))
            crest = peak / (rms + 1e-10)
            mean_abs = float(np.mean(np.abs(sig))) + 1e-10
            shape_f = rms / mean_abs
            impulse = peak / mean_abs

            fft_mag = np.abs(np.fft.rfft(sig))
            power = fft_mag**2
            total_pw = float(np.sum(power)) + 1e-10

            spec_centroid = float(np.sum(freqs * power) / total_pw)
            spec_var = float(np.sum((freqs - spec_centroid) ** 2 * power) / total_pw)
            dom_freq = float(freqs[int(np.argmax(fft_mag))])
            p_norm = power / total_pw
            spec_entropy = float(-np.sum(p_norm * np.log(p_norm + 1e-10)))

            band_energies = [
                float(np.sum(power[band_edges[k] : band_edges[k + 1]]) / total_pw)
                for k in range(N_FREQ_BANDS)
            ]

            sample_features.extend(
                [
                    mean_val,
                    std_val,
                    rms,
                    peak,
                    ptp,
                    kurt,
                    skew,
                    crest,
                    shape_f,
                    impulse,
                    spec_centroid,
                    spec_var,
                    dom_freq,
                    spec_entropy,
                    *band_energies,
                ]
            )
        all_features.append(sample_features)

    features = np.array(all_features, dtype=np.float32)
    feat_mean = np.mean(features, axis=1, keepdims=True)
    feat_std = np.std(features, axis=1, keepdims=True) + 1e-8
    return (features - feat_mean) / feat_std
