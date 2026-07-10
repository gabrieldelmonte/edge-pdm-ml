"""MAFAULDA dataset loader.

Loads vibration data recorded at 50 kHz. The following channels are available
in the CSV files:

  col 0: tachometer signal
  col 1: underhang bearing accelerometer (axial)
  col 2: underhang bearing accelerometer (radial)
  col 3: underhang bearing accelerometer (tangential)
  col 4: overhang bearing accelerometer (axial)
  col 5: overhang bearing accelerometer (radial)
  col 6: overhang bearing accelerometer (tangential)

The dataset is organised as::

  mafaulda/
    normal/                        -> class 0
    horizontal-misalignment/<sev>/ -> class 1
    vertical-misalignment/<sev>/   -> class 2
    imbalance/<sev>/               -> class 3
    overhang/ball_fault/<sev>/     -> class 4
    overhang/cage_fault/<sev>/     -> class 5
    overhang/outer_race/<sev>/     -> class 6
    underhang/ball_fault/<sev>/    -> class 7
    underhang/cage_fault/<sev>/    -> class 8
    underhang/outer_race/<sev>/    -> class 9
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd
from sklearn.model_selection import StratifiedGroupKFold
from sklearn.preprocessing import StandardScaler
from tqdm import tqdm

# Make shared importable when this module is run directly (e.g. python -m datasets.mafaulda_loader)
_AI_DIR = Path(__file__).resolve().parents[1]
if str(_AI_DIR) not in sys.path:
    sys.path.insert(0, str(_AI_DIR))

from shared.constants import (  # noqa: E402
    CLASSES,
    N_CHANNELS,
    N_CLASSES,
    SOURCE_SAMPLE_RATE,
    WINDOW_SIZE,
    WINDOW_STRIDE,
)
from shared.feature_extraction import extract_features, resample_channels  # noqa: E402

logger = logging.getLogger(__name__)

# Re-export constants so existing callers (training.py, evaluation.py) keep working
__all__ = [
    "CLASSES",
    "N_CHANNELS",
    "N_CLASSES",
    "SOURCE_SAMPLE_RATE",
    "WINDOW_SIZE",
    "WINDOW_STRIDE",
    "extract_features",
    "load_mafaulda",
    "prepare_data",
]

# Module-level aliases expected by legacy imports
SAMPLE_RATE: int = SOURCE_SAMPLE_RATE
STRIDE: int = WINDOW_STRIDE

DATASET_ROOT: Path = Path(__file__).parent / "mafaulda"

CHANNEL_NAMES: List[str] = [
    "tachometer",
    "underhang_axial",
    "underhang_radial",
    "underhang_tangential",
    "overhang_axial",
    "overhang_radial",
    "overhang_tangential",
]

CLASS_TO_IDX: Dict[str, int] = {c: i for i, c in enumerate(CLASSES)}


# Internal helpers


def _get_class_label(csv_path: Path, dataset_root: Path) -> Optional[str]:
    """Infer the class label from a CSV file path inside the dataset root.

    Args:
        csv_path: Absolute path to a CSV file inside the dataset.
        dataset_root: Root path of the MAFAULDA dataset directory.

    Returns:
        Class label string, or None if the path cannot be mapped to a class.
    """
    parts = csv_path.relative_to(dataset_root).parts
    top = parts[0]
    if top == "normal":
        return "normal"
    if top == "horizontal-misalignment":
        return "horizontal-misalignment"
    if top == "vertical-misalignment":
        return "vertical-misalignment"
    if top == "imbalance":
        return "imbalance"
    if top in ("overhang", "underhang") and len(parts) >= 2:
        fault_type = parts[1]
        return f"{top}-{fault_type}"
    return None


def _grouped_stratified_split(
    y: np.ndarray,
    groups: np.ndarray,
    test_size: float,
    random_state: int,
) -> Tuple[np.ndarray, np.ndarray]:
    """Split indices into (train, test) keeping groups intact and classes balanced.

    Uses a single fold of ``StratifiedGroupKFold`` so that (a) no recording
    (group) spans both sides and (b) each class keeps roughly its global
    proportion on both sides. The number of folds is chosen so one held-out
    fold approximates ``test_size``.

    Args:
        y: Integer class labels, shape (n_samples,).
        groups: Recording id per sample, shape (n_samples,).
        test_size: Target fraction of samples in the test side, in (0, 1).
        random_state: Seed for the fold shuffling.

    Returns:
        (train_idx, test_idx) integer index arrays into ``y``.
    """
    n_splits = max(2, round(1.0 / test_size))
    sgkf = StratifiedGroupKFold(n_splits=n_splits, shuffle=True, random_state=random_state)
    train_idx, test_idx = next(sgkf.split(np.zeros(len(y)), y, groups))
    return train_idx, test_idx


# Public API


def load_mafaulda(
    dataset_root: Optional[Path] = None,
    window_size: int = WINDOW_SIZE,
    stride: int = WINDOW_STRIDE,
    max_files_per_class: Optional[int] = None,
    verbose: bool = True,
    used_columns: tuple = (1, 2, 3),
    sample_rate: int = SAMPLE_RATE,
) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Load the MAFAULDA dataset and return windowed samples.

    Args:
        dataset_root: Path to the ``mafaulda/`` folder. Defaults to the sibling
            directory next to this module.
        window_size: Number of time steps per window.
        stride: Step between consecutive windows; use ``window_size`` for no
            overlap.
        max_files_per_class: Cap on the number of CSV files loaded per class.
            ``None`` means load all files.
        verbose: Log progress information.
        used_columns: Column indices to read from each CSV. Defaults to
            ``(1, 2, 3)`` for underhang axial/radial/tangential (3 channels,
            matching N_CHANNELS=3 and the inference pipeline). Use ``(4, 5, 6)``
            for overhang.
        sample_rate: Target sample rate in Hz. The raw 50 kHz MAFAULDA signals
            are downsampled to this rate before windowing. Defaults to
            ``SAMPLE_RATE`` (5 000 Hz), giving 204 ms windows at 1 024 samples.

    Returns:
        X: ndarray of shape (n_samples, window_size, n_channels) containing
            raw windowed signals.
        y: ndarray of shape (n_samples,) with integer class labels.
        groups: ndarray of shape (n_samples,) with an integer recording id.
            All windows extracted from the same CSV file share the same id, so
            downstream splitting can keep an entire recording within a single
            train/val/test split and avoid window-level leakage.
    """
    if dataset_root is None:
        dataset_root = DATASET_ROOT

    dataset_root = Path(dataset_root)
    windows: List[np.ndarray] = []
    labels: List[int] = []
    groups: List[int] = []
    file_counts: Dict[str, int] = {c: 0 for c in CLASSES}

    all_csv_paths = list(dataset_root.rglob("*.csv"))

    iterator = (
        tqdm(sorted(all_csv_paths), desc="Loading CSV files")
        if verbose
        else sorted(all_csv_paths)
    )

    file_id = 0
    for csv_path in iterator:
        label = _get_class_label(csv_path, dataset_root)
        if label is None:
            continue
        if max_files_per_class is not None and file_counts[label] >= max_files_per_class:
            continue

        data = pd.read_csv(csv_path, header=None, usecols=list(used_columns)).values.astype(
            np.float32
        )

        if sample_rate != SOURCE_SAMPLE_RATE:
            data = resample_channels(data, SOURCE_SAMPLE_RATE, sample_rate)

        n_rows = len(data)
        for start in range(0, n_rows - window_size + 1, stride):
            windows.append(data[start : start + window_size])
            labels.append(CLASS_TO_IDX[label])
            groups.append(file_id)

        file_counts[label] += 1
        file_id += 1

    if verbose:
        total_files = sum(file_counts.values())
        logger.info("Loaded %d windows from %d files", len(windows), total_files)
        for cls, cnt in file_counts.items():
            logger.info("  %s: %d files", cls, cnt)

    X = np.stack(windows, axis=0)
    y = np.array(labels, dtype=np.int64)
    g = np.array(groups, dtype=np.int64)
    return X, y, g


def prepare_data(
    dataset_root: Optional[Path] = None,
    window_size: int = WINDOW_SIZE,
    stride: int = WINDOW_STRIDE,
    max_files_per_class: Optional[int] = None,
    test_size: float = 0.15,
    val_size: float = 0.15,
    random_state: int = 42,
    verbose: bool = True,
    used_columns: tuple = (1, 2, 3),
    sample_rate: int = SAMPLE_RATE,
) -> Dict:
    """Run the full pipeline: load, extract features, split, and normalise.

    Splitting is done at the *recording* level with ``GroupShuffleSplit``: every
    window from a given CSV file lands in exactly one of train/val/test. This
    prevents window-level leakage, where near-identical sibling windows from the
    same recording would otherwise be scattered across splits and let a model
    memorise per-recording fingerprints instead of generalising.

    Args:
        dataset_root: Path to the ``mafaulda/`` folder.
        window_size: Number of time steps per window.
        stride: Step between consecutive windows.
        max_files_per_class: Cap on CSV files per class; ``None`` uses all.
        test_size: Fraction of data reserved for the test split.
        val_size: Fraction of data reserved for the validation split.
        random_state: Random seed threaded through all ``train_test_split``
            calls for reproducibility.
        verbose: Log progress information.
        used_columns: Column indices to read. Defaults to ``(1, 2, 3)`` for
            underhang axial/radial/tangential; use ``(4, 5, 6)`` for overhang.

    Returns:
        A dictionary containing:

        - ``X_raw_train/val/test``: shape (N, window_size, n_channels) for
          DL models.
        - ``X_feat_train/val/test``: shape (N, n_features) for ML models.
        - ``y_train/val/test``: integer class labels.
        - ``scaler_raw``: fitted StandardScaler (channel-wise on windows).
        - ``scaler_feat``: fitted StandardScaler (feature-wise).
    """
    X_raw, y, groups = load_mafaulda(
        dataset_root=dataset_root,
        window_size=window_size,
        stride=stride,
        max_files_per_class=max_files_per_class,
        verbose=verbose,
        used_columns=used_columns,
        sample_rate=sample_rate,
    )

    if verbose:
        logger.info("Extracting features (sample_rate=%d Hz)...", sample_rate)
    X_feat = extract_features(X_raw, sample_rate=sample_rate)

    # Group-aware, class-stratified split: keep every window of a recording
    # within one split (no leakage) while preserving each class's proportion
    # across train/val/test - important for the scarce ``normal`` class.
    tv_idx, test_idx = _grouped_stratified_split(y, groups, test_size, random_state)

    X_raw_tv, X_raw_test = X_raw[tv_idx], X_raw[test_idx]
    X_feat_tv, X_feat_test = X_feat[tv_idx], X_feat[test_idx]
    y_tv, y_test = y[tv_idx], y[test_idx]
    groups_tv = groups[tv_idx]

    rel_val = val_size / (1.0 - test_size)
    train_idx, val_idx = _grouped_stratified_split(y_tv, groups_tv, rel_val, random_state)

    X_raw_train, X_raw_val = X_raw_tv[train_idx], X_raw_tv[val_idx]
    X_feat_train, X_feat_val = X_feat_tv[train_idx], X_feat_tv[val_idx]
    y_train, y_val = y_tv[train_idx], y_tv[val_idx]

    scaler_feat = StandardScaler()
    X_feat_train = scaler_feat.fit_transform(X_feat_train)
    X_feat_val = scaler_feat.transform(X_feat_val)
    X_feat_test = scaler_feat.transform(X_feat_test)

    n_tr, ws, nc = X_raw_train.shape
    scaler_raw = StandardScaler()
    X_raw_train = scaler_raw.fit_transform(X_raw_train.reshape(-1, nc)).reshape(n_tr, ws, nc)
    X_raw_val = scaler_raw.transform(X_raw_val.reshape(-1, nc)).reshape(
        X_raw_val.shape[0], ws, nc
    )
    X_raw_test = scaler_raw.transform(X_raw_test.reshape(-1, nc)).reshape(
        X_raw_test.shape[0], ws, nc
    )

    # Sanity check: no recording may appear in more than one split.
    g_train = set(groups_tv[train_idx].tolist())
    g_val = set(groups_tv[val_idx].tolist())
    g_test = set(groups[test_idx].tolist())
    assert not (g_train & g_val), "Recording leak between train and val"
    assert not (g_train & g_test), "Recording leak between train and test"
    assert not (g_val & g_test), "Recording leak between val and test"

    if verbose:
        logger.info(
            "Split -> train: %d  val: %d  test: %d samples",
            len(y_train),
            len(y_val),
            len(y_test),
        )
        logger.info(
            "Recordings -> train: %d  val: %d  test: %d (disjoint)",
            len(g_train),
            len(g_val),
            len(g_test),
        )

    return {
        "X_raw_train": X_raw_train,
        "X_raw_val": X_raw_val,
        "X_raw_test": X_raw_test,
        "X_feat_train": X_feat_train,
        "X_feat_val": X_feat_val,
        "X_feat_test": X_feat_test,
        "y_train": y_train,
        "y_val": y_val,
        "y_test": y_test,
        "scaler_raw": scaler_raw,
        "scaler_feat": scaler_feat,
    }
