"""Training entry-point for all MAFAULDA fault-detection models.

Usage
-----
Run from the ``src/ai/`` directory::

    python -m training.training

Trained models are saved under ``saved_models/``.
Training-time statistics (accuracy on validation set, elapsed seconds) are
logged to stdout and written to ``saved_models/training_results.json``.

Configurable constants are declared at the top of the file.
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from threading import Lock

import numpy as np

# Make the src/ai package importable when run directly
_AI_DIR = Path(__file__).resolve().parents[1]
if str(_AI_DIR) not in sys.path:
    sys.path.insert(0, str(_AI_DIR))

from shared.constants import N_CHANNELS, N_CLASSES, SAMPLE_RATE, SOURCE_SAMPLE_RATE, WINDOW_SIZE  # noqa: E402

from datasets.mafaulda_loader import prepare_data  # noqa: E402

from models.machine_learning.random_forest import RandomForestModel  # noqa: E402
from models.machine_learning.svm import SVMModel  # noqa: E402
from models.machine_learning.xgboost import XGBoostModel  # noqa: E402
from models.machine_learning.lightbm import LightGBMModel  # noqa: E402

from models.deep_learning.cnn1d import CNN1DModel  # noqa: E402
from models.deep_learning.lstm import LSTMModel  # noqa: E402
from models.deep_learning.gru import GRUModel  # noqa: E402
from models.deep_learning.rnn import RNNModel  # noqa: E402

logger = logging.getLogger(__name__)

# Repository root for git introspection
REPO_ROOT = Path(__file__).resolve().parents[3]

# Configuration

DATASET_ROOT = _AI_DIR / "datasets" / "mafaulda"
SAVED_MODELS_DIR = _AI_DIR / "saved_models"

# Limit CSV files per class to keep memory usage manageable.
# Set to None to use the full dataset (requires ~8 GB RAM).
MAX_FILES_PER_CLASS = None

# Deep-learning hyper-parameters
DL_EPOCHS = 200
DL_BATCH_SIZE = 80
DL_PATIENCE = 15
DL_LR = 1e-4

# Parallelism
CPU_WORKERS = os.cpu_count() or 1
ML_PARALLEL_WORKERS = min(4, CPU_WORKERS)
ML_THREADS_PER_MODEL = max(1, CPU_WORKERS // ML_PARALLEL_WORKERS)


# Helpers


def _accuracy(y_true: np.ndarray, y_pred: np.ndarray) -> float:
    """Compute accuracy as fraction of correctly classified samples.

    Args:
        y_true: Ground-truth integer labels.
        y_pred: Predicted integer labels.

    Returns:
        Accuracy in [0, 1].
    """
    return float(np.mean(y_true == y_pred))


def _train_and_save(
    model: object,
    X_train: np.ndarray,
    y_train: np.ndarray,
    X_val: np.ndarray,
    y_val: np.ndarray,
    save_path: str,
) -> dict:
    """Train and save a model, logging progress to stdout (sequential models).

    Args:
        model: Model instance with ``train``, ``predict``, and ``save`` methods.
        X_train: Training inputs.
        y_train: Training labels.
        X_val: Validation inputs.
        y_val: Validation labels.
        save_path: File path where the trained model is persisted.

    Returns:
        Dictionary with keys ``model``, ``val_accuracy``, and ``train_time_s``.
    """
    logger.info("\n%s", "=" * 60)
    logger.info("Training: %s", model.name)
    logger.info("%s", "=" * 60)
    t0 = time.perf_counter()
    model.train(X_train, y_train, X_val=X_val, y_val=y_val)
    elapsed = time.perf_counter() - t0

    val_preds = model.predict(X_val)
    val_acc = _accuracy(y_val, val_preds)
    logger.info("  Val accuracy : %.4f", val_acc)
    logger.info("  Training time: %.1f s", elapsed)
    model.save(save_path)
    logger.info("  Saved to     : %s", save_path)

    return {"model": model.name, "val_accuracy": val_acc, "train_time_s": round(elapsed, 2)}


def _train_and_save_buffered(
    model: object,
    X_train: np.ndarray,
    y_train: np.ndarray,
    X_val: np.ndarray,
    y_val: np.ndarray,
    save_path: str,
    print_lock: Lock,
) -> dict:
    """Train and save a model for use in parallel ML workers.

    ML models (sklearn/XGBoost/LightGBM) produce no per-epoch output, so
    all output is written atomically under a lock to avoid interleaving.

    Args:
        model: Model instance with ``train``, ``predict``, and ``save`` methods.
        X_train: Training inputs.
        y_train: Training labels.
        X_val: Validation inputs.
        y_val: Validation labels.
        save_path: File path where the trained model is persisted.
        print_lock: Lock used to serialise log output across threads.

    Returns:
        Dictionary with keys ``model``, ``val_accuracy``, and ``train_time_s``.
    """
    with print_lock:
        logger.info("\n%s", "=" * 60)
        logger.info("  [ STARTED ] %s", model.name)
        logger.info("%s", "=" * 60)

    t0 = time.perf_counter()
    model.train(X_train, y_train, X_val=X_val, y_val=y_val)
    elapsed = time.perf_counter() - t0

    val_preds = model.predict(X_val)
    val_acc = _accuracy(y_val, val_preds)
    model.save(save_path)

    with print_lock:
        logger.info("\n%s", "=" * 60)
        logger.info("  [ DONE     ] %s", model.name)
        logger.info("%s", "=" * 60)
        logger.info("  Val accuracy : %.4f", val_acc)
        logger.info("  Training time: %.1f s", elapsed)
        logger.info("  Saved to     : %s", save_path)

    return {"model": model.name, "val_accuracy": val_acc, "train_time_s": round(elapsed, 2)}


# Per-family training functions


def train_machine_learning(data: dict, results: list, models: list | None = None) -> None:
    """Train ML models in parallel on the extracted feature matrix.

    Each model is submitted to a ``ThreadPoolExecutor``. scikit-learn,
    XGBoost, and LightGBM release the GIL during their core C/BLAS routines,
    so threads run truly in parallel. CPU cores are divided evenly among the
    models via ``ML_THREADS_PER_MODEL``.

    Args:
        data: Dictionary returned by ``prepare_data`` containing feature arrays
            and labels.
        results: List to which per-model result dictionaries are appended
            in-place.
        models: Optional list of (model, filename) pairs to train. Defaults to
            all four ML models.
    """
    from tqdm import tqdm

    X_tr = data["X_feat_train"]
    y_tr = data["y_train"]
    X_v = data["X_feat_val"]
    y_v = data["y_val"]

    if models is None:
        models = [
            (RandomForestModel(n_jobs=ML_THREADS_PER_MODEL), "rf_model.pkl"),
            (SVMModel(), "svm_model.pkl"),
            (XGBoostModel(n_jobs=ML_THREADS_PER_MODEL), "xgboost_model.pkl"),
            (LightGBMModel(n_jobs=ML_THREADS_PER_MODEL), "lightgbm_model.pkl"),
        ]

    if not models:
        return

    logger.info(
        "\nTraining %d ML models in parallel (%d workers, %d threads each)...",
        len(models),
        ML_PARALLEL_WORKERS,
        ML_THREADS_PER_MODEL,
    )

    print_lock = Lock()
    futures: dict = {}
    with ThreadPoolExecutor(max_workers=ML_PARALLEL_WORKERS) as pool:
        for model, filename in models:
            save_path = str(SAVED_MODELS_DIR / filename)
            fut = pool.submit(
                _train_and_save_buffered,
                model,
                X_tr,
                y_tr,
                X_v,
                y_v,
                save_path,
                print_lock,
            )
            futures[fut] = model.name

        for fut in tqdm(as_completed(futures), total=len(futures), desc="Training ML models"):
            results.append(fut.result())


def train_deep_learning(data: dict, results: list, models: list | None = None) -> None:
    """Train DL models sequentially on the raw windowed signals.

    Args:
        data: Dictionary returned by ``prepare_data`` containing raw window
            arrays and labels.
        results: List to which per-model result dictionaries are appended
            in-place.
        models: Optional list of (model, filename) pairs to train. Defaults to
            all four DL models.
    """
    X_tr = data["X_raw_train"]
    y_tr = data["y_train"]
    X_v = data["X_raw_val"]
    y_v = data["y_val"]

    dl_kwargs = dict(
        n_classes=N_CLASSES,
        window_size=WINDOW_SIZE,
        n_channels=N_CHANNELS,
        epochs=DL_EPOCHS,
        batch_size=DL_BATCH_SIZE,
        patience=DL_PATIENCE,
        lr=DL_LR,
    )

    if models is None:
        models = [
            (CNN1DModel(**dl_kwargs), "cnn1d_model.pt"),
            (LSTMModel(**dl_kwargs), "lstm_model.pt"),
            (GRUModel(**dl_kwargs), "gru_model.pt"),
            (RNNModel(**dl_kwargs), "rnn_model.pt"),
        ]

    if not models:
        return

    for model, filename in models:
        save_path = str(SAVED_MODELS_DIR / filename)
        res = _train_and_save(model, X_tr, y_tr, X_v, y_v, save_path)
        results.append(res)


# Main


def argument_parser():
    """Build and return the CLI argument parser for the training script.

    Returns:
        Parsed argument namespace with at least ``type``, ``model``,
        ``sample_rate``, and ``dry_run`` attributes.
    """
    import argparse

    parser = argparse.ArgumentParser(description="Choose model type for training.")
    parser.add_argument(
        "--type",
        choices=["underhang", "overhang"],
        default="underhang",
        help="Bearing type to train (default: underhang)",
    )
    parser.add_argument(
        "--model",
        choices=["machine-learning", "deep-learning", "both"],
        default="both",
        help="Model family to train (default: both)",
    )
    parser.add_argument(
        "--sample-rate",
        type=int,
        default=None,
        metavar="HZ",
        help="Target sample rate in Hz for feature extraction (default: SOURCE_SAMPLE_RATE)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print the training plan (models, hyperparams, split sizes) and exit.",
    )
    return parser.parse_args()


def _get_git_commit() -> str:
    """Return the current HEAD commit hash, or 'unknown' on failure.

    Returns:
        40-character hex SHA of HEAD, or the string ``'unknown'``.
    """
    try:
        return (
            subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=str(REPO_ROOT))
            .decode()
            .strip()
        )
    except Exception:
        return "unknown"


def main_training() -> None:
    """Entry point: parse arguments, load data, train models, and save results."""
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    args = argument_parser()

    logger.info("Loading and preparing MAFAULDA dataset...")

    if args.type == "underhang":
        logger.info("--> Type: Underhang bearing selected (columns 1, 2, 3: axial/radial/tangential)")
        used_cols = (1, 2, 3)
        type_str = "underhang"
    elif args.type == "overhang":
        logger.info("--> Type: Overhang bearing selected (columns 4, 5, 6: axial/radial/tangential)")
        used_cols = (4, 5, 6)
        type_str = "overhang"
    else:
        raise ValueError(f"Invalid type: {args.type}")

    sample_rate = args.sample_rate if args.sample_rate is not None else SAMPLE_RATE
    logger.info("--> Model family : %s", args.model)
    logger.info("--> Sample rate  : %d Hz (source: %d Hz)", sample_rate, SOURCE_SAMPLE_RATE)

    global SAVED_MODELS_DIR
    SAVED_MODELS_DIR = SAVED_MODELS_DIR / type_str

    dl_kwargs = dict(
        n_classes=N_CLASSES,
        window_size=WINDOW_SIZE,
        n_channels=N_CHANNELS,
        epochs=DL_EPOCHS,
        batch_size=DL_BATCH_SIZE,
        patience=DL_PATIENCE,
        lr=DL_LR,
    )
    all_ml = [
        (RandomForestModel(n_jobs=ML_THREADS_PER_MODEL), "rf_model.pkl"),
        (SVMModel(), "svm_model.pkl"),
        (XGBoostModel(n_jobs=ML_THREADS_PER_MODEL), "xgboost_model.pkl"),
        (LightGBMModel(n_jobs=ML_THREADS_PER_MODEL), "lightgbm_model.pkl"),
    ]
    all_dl = [
        (CNN1DModel(**dl_kwargs), "cnn1d_model.pt"),
        (LSTMModel(**dl_kwargs), "lstm_model.pt"),
        (GRUModel(**dl_kwargs), "gru_model.pt"),
        (RNNModel(**dl_kwargs), "rnn_model.pt"),
    ]

    # Apply model family filter
    if args.model == "machine-learning":
        all_dl = []
    elif args.model == "deep-learning":
        all_ml = []

    if args.dry_run:
        logger.info("=== DRY RUN ===")
        logger.info("Type           : %s", type_str)
        logger.info("Dataset root   : %s", DATASET_ROOT)
        logger.info("Max files/class: %s", MAX_FILES_PER_CLASS)
        logger.info("Sample rate    : %d Hz", sample_rate)
        logger.info("DL hyper-params: epochs=%d  batch=%d  patience=%d  lr=%g",
                    DL_EPOCHS, DL_BATCH_SIZE, DL_PATIENCE, DL_LR)
        logger.info("ML workers     : %d parallel / %d threads each",
                    ML_PARALLEL_WORKERS, ML_THREADS_PER_MODEL)
        logger.info("ML models      : %s", [m.name for m, _ in all_ml])
        logger.info("DL models      : %s", [m.name for m, _ in all_dl])
        return

    data = prepare_data(
        dataset_root=DATASET_ROOT,
        max_files_per_class=MAX_FILES_PER_CLASS,
        verbose=True,
        used_columns=used_cols,
        sample_rate=sample_rate,
    )

    results: list = []

    train_deep_learning(data, results, models=all_dl)
    train_machine_learning(data, results, models=all_ml)

    git_commit = _get_git_commit()
    for r in results:
        r["git_commit"] = git_commit

    summary_path = f"{SAVED_MODELS_DIR}/training_results.json"
    with open(summary_path, "w") as f:
        json.dump(results, f, indent=4)
    logger.info("\nTraining summary saved to %s", summary_path)

    logger.info("\n=== Training complete ===")
    logger.info("%-20s %10s %12s", "Model", "Val Acc", "Time (s)")
    logger.info("-" * 44)
    for r in results:
        logger.info("%-20s %10.4f %12.1f", r["model"], r["val_accuracy"], r["train_time_s"])


if __name__ == "__main__":
    main_training()
