"""Grouped, stratified k-fold cross-validation for MAFAULDA models.

Unlike ``evaluation.py`` (which scores the single saved/deployed split), this
script trains each model ``n_splits`` times on a ``StratifiedGroupKFold`` and
reports per-class metrics as mean +/- std across folds. Every recording serves
as a test recording exactly once, so per-class numbers - especially for the
scarce ``normal`` class - become trustworthy instead of resting on one lucky
draw.

Usage
-----
Run from ``src/ai/``::

    # Cheap: the feature-based ML models only (minutes)
    python -m evaluation.cross_validation --type overhang --model machine-learning

    # Expensive: include the deep-learning models (hours)
    python -m evaluation.cross_validation --type overhang --model both

Results are written to ``evaluation/plots/{type}/cross_validation.txt``.
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

import numpy as np
from sklearn.metrics import classification_report
from sklearn.model_selection import StratifiedGroupKFold
from sklearn.preprocessing import StandardScaler

# Make the src/ai package importable when run directly
_AI_DIR = Path(__file__).resolve().parents[1]
if str(_AI_DIR) not in sys.path:
    sys.path.insert(0, str(_AI_DIR))

from shared.constants import (  # noqa: E402
    CLASSES,
    N_CHANNELS,
    N_CLASSES,
    SAMPLE_RATE,
    SOURCE_SAMPLE_RATE,
    WINDOW_SIZE,
)
from shared.feature_extraction import extract_features  # noqa: E402
from datasets.mafaulda_loader import load_mafaulda  # noqa: E402

from models.machine_learning.random_forest import RandomForestModel  # noqa: E402
from models.machine_learning.svm import SVMModel  # noqa: E402
from models.machine_learning.xgboost import XGBoostModel  # noqa: E402
from models.machine_learning.lightbm import LightGBMModel  # noqa: E402
from models.deep_learning.cnn1d import CNN1DModel  # noqa: E402
from models.deep_learning.lstm import LSTMModel  # noqa: E402
from models.deep_learning.gru import GRUModel  # noqa: E402
from models.deep_learning.rnn import RNNModel  # noqa: E402

# Reuse the exact hyper-parameters training.py uses, so CV reflects reality.
from training.training import (  # noqa: E402
    DL_BATCH_SIZE,
    DL_EPOCHS,
    DL_LR,
    DL_PATIENCE,
    ML_THREADS_PER_MODEL,
)

logger = logging.getLogger(__name__)

DATASET_ROOT = _AI_DIR / "datasets" / "mafaulda"
PLOTS_DIR = Path(__file__).resolve().parent / "plots"

# Must match training.py
MAX_FILES_PER_CLASS = None


def _dl_kwargs() -> dict:
    """Return DL constructor kwargs matching training.py."""
    return dict(
        n_classes=N_CLASSES,
        window_size=WINDOW_SIZE,
        n_channels=N_CHANNELS,
        epochs=DL_EPOCHS,
        batch_size=DL_BATCH_SIZE,
        patience=DL_PATIENCE,
        lr=DL_LR,
    )


def _make_models(model_family: str) -> list:
    """Build a list of ``(factory, uses_raw)`` pairs for the requested family.

    Factories are used so each fold trains a fresh, unfitted model instance.

    Args:
        model_family: ``"machine-learning"``, ``"deep-learning"`` or ``"both"``.

    Returns:
        List of ``(callable -> model, uses_raw_windows)`` tuples.
    """
    ml = [
        (lambda: RandomForestModel(n_jobs=ML_THREADS_PER_MODEL), False),
        (lambda: SVMModel(), False),
        (lambda: XGBoostModel(n_jobs=ML_THREADS_PER_MODEL), False),
        (lambda: LightGBMModel(n_jobs=ML_THREADS_PER_MODEL), False),
    ]
    dl = [
        (lambda: CNN1DModel(**_dl_kwargs()), True),
        (lambda: LSTMModel(**_dl_kwargs()), True),
        (lambda: GRUModel(**_dl_kwargs()), True),
        (lambda: RNNModel(**_dl_kwargs()), True),
    ]
    if model_family == "machine-learning":
        return ml
    if model_family == "deep-learning":
        return dl
    return ml + dl


def _scale_raw(arr: np.ndarray, scaler: StandardScaler, fit: bool) -> np.ndarray:
    """Channel-wise standardise a raw-window array with a shared scaler.

    Args:
        arr: Raw windows, shape (n, window_size, n_channels).
        scaler: StandardScaler to fit (train) or apply (test).
        fit: If True, fit-then-transform; otherwise transform only.

    Returns:
        Standardised array with the same shape as ``arr``.
    """
    flat = arr.reshape(-1, arr.shape[-1])
    flat = scaler.fit_transform(flat) if fit else scaler.transform(flat)
    return flat.reshape(arr.shape)


def cross_validate(type_str: str, model_family: str, sample_rate: int, n_splits: int) -> None:
    """Run grouped stratified CV and write a per-class mean+/-std report.

    Args:
        type_str: ``"underhang"`` or ``"overhang"``.
        model_family: Which model family to cross-validate.
        sample_rate: Target sample rate in Hz (must match how models are trained).
        n_splits: Number of CV folds.
    """
    used_columns = (1, 2, 3) if type_str == "underhang" else (4, 5, 6)
    logger.info("Loading MAFAULDA (columns %s, sample_rate=%d Hz)...", used_columns, sample_rate)

    X_raw, y, groups = load_mafaulda(
        dataset_root=DATASET_ROOT,
        max_files_per_class=MAX_FILES_PER_CLASS,
        verbose=True,
        used_columns=used_columns,
        sample_rate=sample_rate,
    )
    logger.info("Extracting features...")
    X_feat = extract_features(X_raw, sample_rate=sample_rate)

    models = _make_models(model_family)
    # metrics[model_name][class_name] = {"precision": [...], "recall": [...], "f1": [...]}
    metrics: dict = {}
    accuracies: dict = {}

    sgkf = StratifiedGroupKFold(n_splits=n_splits, shuffle=True, random_state=42)

    for fold, (tr_idx, te_idx) in enumerate(sgkf.split(X_raw, y, groups), start=1):
        logger.info("\n%s Fold %d/%d %s", "=" * 20, fold, n_splits, "=" * 20)
        y_tr, y_te = y[tr_idx], y[te_idx]

        # Fit scalers on this fold's training data only.
        scaler_feat = StandardScaler()
        X_feat_tr = scaler_feat.fit_transform(X_feat[tr_idx])
        X_feat_te = scaler_feat.transform(X_feat[te_idx])

        scaler_raw = StandardScaler()
        X_raw_tr = _scale_raw(X_raw[tr_idx], scaler_raw, fit=True)
        X_raw_te = _scale_raw(X_raw[te_idx], scaler_raw, fit=False)

        for factory, uses_raw in models:
            model = factory()
            name = model.name
            logger.info("  [Fold %d] training %s ...", fold, name)

            if uses_raw:
                model.train(X_raw_tr, y_tr)
                y_pred = model.predict(X_raw_te)
            else:
                model.train(X_feat_tr, y_tr)
                y_pred = model.predict(X_feat_te)

            report = classification_report(
                y_te,
                y_pred,
                labels=list(range(N_CLASSES)),
                target_names=CLASSES,
                output_dict=True,
                zero_division=0,
            )
            m = metrics.setdefault(name, {c: {"precision": [], "recall": [], "f1": []} for c in CLASSES})
            for c in CLASSES:
                m[c]["precision"].append(report[c]["precision"])
                m[c]["recall"].append(report[c]["recall"])
                m[c]["f1"].append(report[c]["f1-score"])
            accuracies.setdefault(name, []).append(report["accuracy"])

            del model

    _write_report(type_str, model_family, n_splits, metrics, accuracies)


def _fmt(values: list[float]) -> str:
    """Format a list of per-fold values as ``mean+/-std``."""
    arr = np.asarray(values, dtype=float)
    return f"{arr.mean():.3f}+/-{arr.std():.3f}"


def _write_report(
    type_str: str, model_family: str, n_splits: int, metrics: dict, accuracies: dict
) -> None:
    """Write the aggregated CV report to disk and stdout."""
    out_dir = PLOTS_DIR / type_str
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / "cross_validation.txt"

    lines: list[str] = []
    lines.append(f"Grouped Stratified {n_splits}-Fold Cross-Validation")
    lines.append(f"type={type_str}  family={model_family}")
    lines.append("Values are mean+/-std across folds (per-fold test sets are recording-disjoint).")

    for name in metrics:
        lines.append("\n" + "=" * 78)
        lines.append(name)
        lines.append("=" * 78)
        lines.append(f"  accuracy: {_fmt(accuracies[name])}")
        lines.append(f"\n  {'class':<24}{'precision':>16}{'recall':>16}{'f1-score':>16}")
        macro_f1 = []
        for c in CLASSES:
            p = _fmt(metrics[name][c]["precision"])
            r = _fmt(metrics[name][c]["recall"])
            f = _fmt(metrics[name][c]["f1"])
            macro_f1.append(np.mean(metrics[name][c]["f1"]))
            lines.append(f"  {c:<24}{p:>16}{r:>16}{f:>16}")
        lines.append(f"\n  macro-F1 (mean over classes): {np.mean(macro_f1):.3f}")

    text = "\n".join(lines)
    out_path.write_text(text + "\n")
    logger.info("\n%s", text)
    logger.info("\nCross-validation report saved to %s", out_path)


def argument_parser():
    """Build and return the CLI argument parser."""
    parser = argparse.ArgumentParser(description="Grouped stratified k-fold CV for MAFAULDA models.")
    parser.add_argument("--type", choices=["underhang", "overhang"], default="underhang")
    parser.add_argument(
        "--model",
        choices=["machine-learning", "deep-learning", "both"],
        default="machine-learning",
        help="Model family to cross-validate (default: machine-learning - cheap).",
    )
    parser.add_argument("--sample-rate", type=int, default=None, metavar="HZ")
    parser.add_argument("--n-splits", type=int, default=5, help="Number of CV folds (default: 5).")
    return parser.parse_args()


def main() -> None:
    """Entry point: parse args and run cross-validation."""
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    args = argument_parser()
    sample_rate = args.sample_rate if args.sample_rate is not None else SAMPLE_RATE
    logger.info("--> Type         : %s", args.type)
    logger.info("--> Model family : %s", args.model)
    logger.info("--> Sample rate  : %d Hz (source: %d Hz)", sample_rate, SOURCE_SAMPLE_RATE)
    logger.info("--> Folds        : %d", args.n_splits)
    cross_validate(args.type, args.model, sample_rate, args.n_splits)


if __name__ == "__main__":
    main()
