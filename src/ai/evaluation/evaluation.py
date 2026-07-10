"""Evaluation script.

Loads saved models, runs them on the test set, and produces a comprehensive
set of visualisations.

Usage
-----
Run from ``src/ai/``::

    python -m evaluation.evaluation

All plots are saved under ``evaluation/{mode_str}/plots/``.
"""

from __future__ import annotations

import gc
import json
import logging
import os
import sys
import time
from pathlib import Path

import matplotlib

matplotlib.use("Agg")  # non-interactive backend - safe for headless servers
import matplotlib.pyplot as plt
import numpy as np
import seaborn as sns
from sklearn.metrics import (
    accuracy_score,
    average_precision_score,
    classification_report,
    confusion_matrix,
    f1_score,
    precision_recall_curve,
    precision_score,
    recall_score,
    roc_auc_score,
    roc_curve,
)
from sklearn.preprocessing import label_binarize

# Make the src/ai package importable when run directly
_AI_DIR = Path(__file__).resolve().parents[1]
if str(_AI_DIR) not in sys.path:
    sys.path.insert(0, str(_AI_DIR))

from shared.constants import CLASSES, N_CLASSES, SAMPLE_RATE, SOURCE_SAMPLE_RATE  # noqa: E402

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

# Configuration

DATASET_ROOT = _AI_DIR / "datasets" / "mafaulda"
SAVED_MODELS_DIR = _AI_DIR / "saved_models"
PLOTS_DIR = Path(__file__).resolve().parent / "plots"

# MUST match training
MAX_FILES_PER_CLASS = None

# Model registry: (class, saved filename, uses_raw_windows)
MODEL_REGISTRY = [
    (RandomForestModel, "rf_model.pkl", False),
    (SVMModel, "svm_model.pkl", False),
    (XGBoostModel, "xgboost_model.pkl", False),
    (LightGBMModel, "lightgbm_model.pkl", False),
    (CNN1DModel, "cnn1d_model.pt", True),
    (LSTMModel, "lstm_model.pt", True),
    (GRUModel, "gru_model.pt", True),
    (RNNModel, "rnn_model.pt", True),
]

PALETTE = sns.color_palette("tab10", n_colors=len(MODEL_REGISTRY))


# Helpers


def _load_data(type_str: str, sample_rate: int) -> dict:
    """Load and prepare the MAFAULDA test dataset.

    Args:
        type_str: Bearing type being evaluated — ``"underhang"`` or
            ``"overhang"``. Determines which accelerometer columns are read so
            the evaluation data matches what the models were trained on.
        sample_rate: Target sample rate in Hz. Must match the rate used
            during training, or the extracted features won't line up with
            what the saved models expect.

    Returns:
        Dictionary returned by ``prepare_data`` containing split arrays and
        fitted scalers.
    """
    used_columns = (1, 2, 3) if type_str == "underhang" else (4, 5, 6)
    logger.info("Loading and preparing dataset (columns %s, sample_rate=%d Hz)...",
                used_columns, sample_rate)
    return prepare_data(
        dataset_root=DATASET_ROOT,
        max_files_per_class=MAX_FILES_PER_CLASS,
        verbose=True,
        used_columns=used_columns,
        sample_rate=sample_rate,
    )


def _load_models(data: dict, model_family: str = "both") -> list[dict]:
    """Load saved models and run predictions on the test set.

    Models are loaded, used for inference, then explicitly deleted and garbage-
    collected to limit peak memory usage.

    Args:
        data: Dictionary returned by ``prepare_data``.
        model_family: Which families to evaluate: ``"machine-learning"``,
            ``"deep-learning"``, or ``"both"``.

    Returns:
        List of record dictionaries, each containing ``name``, ``y_pred``,
        ``y_proba``, and ``infer_time``.
    """
    X_feat_test = data["X_feat_test"]
    X_raw_test = data["X_raw_test"]
    y_test = data["y_test"]

    registry = MODEL_REGISTRY
    if model_family == "machine-learning":
        registry = [(c, f, r) for c, f, r in MODEL_REGISTRY if not r]
    elif model_family == "deep-learning":
        registry = [(c, f, r) for c, f, r in MODEL_REGISTRY if r]

    records = []
    for ModelClass, filename, uses_raw in registry:
        path = SAVED_MODELS_DIR / filename
        if not path.exists():
            logger.info("  [SKIP] %s not found - train first.", filename)
            continue

        logger.info("  Loading %s ...", filename)
        try:
            model = ModelClass.load(str(path))
            X_in = X_raw_test if uses_raw else X_feat_test

            t0 = time.perf_counter()
            y_pred = model.predict(X_in)
            infer_time = time.perf_counter() - t0

            y_proba = model.predict_proba(X_in)

            records.append(
                {
                    "name": model.name,
                    "y_pred": y_pred,
                    "y_proba": y_proba,
                    "infer_time": infer_time,
                }
            )
            acc = accuracy_score(y_test, y_pred)
            logger.info("  acc=%.4f  inference=%.2fs", acc, infer_time)
        except Exception as e:
            logger.error("  [ERROR] Failed to load or run %s: %s", filename, e)
        finally:
            del model
            gc.collect()

    return records


# Plot functions


def plot_confusion_matrices(records: list[dict], y_test: np.ndarray) -> None:
    """Plot normalised confusion matrices for all models.

    Args:
        records: List of model record dictionaries from ``_load_models``.
        y_test: Ground-truth integer labels for the test set.
    """
    n = len(records)
    ncols = 2
    nrows = (n + 1) // ncols
    fig, axes = plt.subplots(nrows, ncols, figsize=(14, nrows * 5.5))
    axes = axes.flatten()

    short_labels = [c.replace("-", "\n").replace("_", "\n") for c in CLASSES]

    for idx, rec in enumerate(records):
        cm = confusion_matrix(y_test, rec["y_pred"])
        cm_norm = cm.astype(float) / cm.sum(axis=1, keepdims=True)
        sns.heatmap(
            cm_norm,
            ax=axes[idx],
            annot=True,
            fmt=".2f",
            xticklabels=short_labels,
            yticklabels=short_labels,
            cmap="Blues",
            linewidths=0.4,
            linecolor="grey",
            vmin=0,
            vmax=1,
            cbar=False,
        )
        axes[idx].set_title(rec["name"], fontsize=12, fontweight="bold")
        axes[idx].set_xlabel("Predicted", fontsize=8)
        axes[idx].set_ylabel("True", fontsize=8)
        axes[idx].tick_params(axis="both", labelsize=7)

    for ax in axes[n:]:
        ax.set_visible(False)

    fig.suptitle(
        "Normalised Confusion Matrices", fontsize=14, fontweight="bold", y=1.01
    )
    plt.tight_layout()
    _save("confusion_matrices.png", fig)


def plot_roc_curves(records: list[dict], y_test: np.ndarray) -> None:
    """Plot one-vs-rest ROC curves for all models and classes.

    Args:
        records: List of model record dictionaries from ``_load_models``.
        y_test: Ground-truth integer labels for the test set.
    """
    y_bin = label_binarize(y_test, classes=list(range(N_CLASSES)))

    fig, axes = plt.subplots(2, 5, figsize=(20, 8))
    axes = axes.flatten()

    for idx, rec in enumerate(records):
        ax = axes[idx]
        y_score = rec["y_proba"]

        for cls_idx, cls_name in enumerate(CLASSES):
            fpr, tpr, _ = roc_curve(y_bin[:, cls_idx], y_score[:, cls_idx])
            auc = roc_auc_score(y_bin[:, cls_idx], y_score[:, cls_idx])
            ax.plot(fpr, tpr, lw=1.2, label=f"{cls_name[:12]} ({auc:.2f})")

        ax.plot([0, 1], [0, 1], "k--", lw=0.8)
        ax.set_xlim([0, 1])
        ax.set_ylim([0, 1.02])
        ax.set_title(rec["name"], fontsize=10, fontweight="bold")
        ax.set_xlabel("FPR", fontsize=8)
        ax.set_ylabel("TPR", fontsize=8)
        ax.legend(fontsize=5, loc="lower right")
        ax.tick_params(labelsize=7)

    fig.suptitle(
        "ROC Curves (One-vs-Rest, per class)", fontsize=13, fontweight="bold"
    )
    plt.tight_layout()
    _save("roc_curves.png", fig)


def plot_precision_recall_curves(records: list[dict], y_test: np.ndarray) -> None:
    """Plot one-vs-rest precision-recall curves for all models and classes.

    Args:
        records: List of model record dictionaries from ``_load_models``.
        y_test: Ground-truth integer labels for the test set.
    """
    y_bin = label_binarize(y_test, classes=list(range(N_CLASSES)))

    fig, axes = plt.subplots(2, 5, figsize=(20, 8))
    axes = axes.flatten()

    for idx, rec in enumerate(records):
        ax = axes[idx]
        y_score = rec["y_proba"]

        for cls_idx, cls_name in enumerate(CLASSES):
            prec, rec_vals, _ = precision_recall_curve(
                y_bin[:, cls_idx], y_score[:, cls_idx]
            )
            ap = average_precision_score(y_bin[:, cls_idx], y_score[:, cls_idx])
            ax.plot(rec_vals, prec, lw=1.2, label=f"{cls_name[:12]} ({ap:.2f})")

        ax.set_xlim([0, 1])
        ax.set_ylim([0, 1.02])
        ax.set_title(rec["name"], fontsize=10, fontweight="bold")
        ax.set_xlabel("Recall", fontsize=8)
        ax.set_ylabel("Precision", fontsize=8)
        ax.legend(fontsize=5, loc="lower left")
        ax.tick_params(labelsize=7)

    fig.suptitle(
        "Precision-Recall Curves (One-vs-Rest, per class)",
        fontsize=13,
        fontweight="bold",
    )
    plt.tight_layout()
    _save("precision_recall_curves.png", fig)


def plot_metrics_comparison(records: list[dict], y_test: np.ndarray) -> None:
    """Plot grouped bar chart comparing classification metrics across models.

    Args:
        records: List of model record dictionaries from ``_load_models``.
        y_test: Ground-truth integer labels for the test set.
    """
    names = [r["name"] for r in records]
    accuracy = [accuracy_score(y_test, r["y_pred"]) for r in records]
    macro_f1 = [
        f1_score(y_test, r["y_pred"], average="macro", zero_division=0) for r in records
    ]
    weighted_f1 = [
        f1_score(y_test, r["y_pred"], average="weighted", zero_division=0) for r in records
    ]
    macro_p = [
        precision_score(y_test, r["y_pred"], average="macro", zero_division=0)
        for r in records
    ]
    macro_r = [
        recall_score(y_test, r["y_pred"], average="macro", zero_division=0) for r in records
    ]

    x = np.arange(len(names))
    width = 0.15
    metrics = [accuracy, macro_p, macro_r, macro_f1, weighted_f1]
    metric_names = ["Accuracy", "Macro Precision", "Macro Recall", "Macro F1", "Weighted F1"]

    fig, ax = plt.subplots(figsize=(14, 6))
    for i, (vals, mname) in enumerate(zip(metrics, metric_names)):
        ax.bar(x + i * width, vals, width, label=mname)

    ax.set_xticks(x + width * 2)
    ax.set_xticklabels(names, rotation=25, ha="right", fontsize=9)
    ax.set_ylim(0, 1.12)
    ax.set_ylabel("Score", fontsize=11)
    ax.set_title("Classification Metrics per Model", fontsize=13, fontweight="bold")
    ax.legend(loc="lower right", fontsize=9)
    ax.yaxis.grid(True, linestyle="--", alpha=0.6)
    ax.set_axisbelow(True)
    plt.tight_layout()
    _save("metrics_comparison.png", fig)


def plot_per_class_f1(records: list[dict], y_test: np.ndarray) -> None:
    """Plot per-class F1 scores for each model as a grouped bar chart.

    Args:
        records: List of model record dictionaries from ``_load_models``.
        y_test: Ground-truth integer labels for the test set.
    """
    short = [c.replace("-", "\n").replace("_", "\n") for c in CLASSES]
    n_models = len(records)
    x = np.arange(N_CLASSES)
    width = 0.8 / n_models

    fig, ax = plt.subplots(figsize=(16, 6))
    for i, rec in enumerate(records):
        f1_per_class = f1_score(y_test, rec["y_pred"], average=None, zero_division=0)
        offset = (i - n_models / 2 + 0.5) * width
        ax.bar(
            x + offset, f1_per_class, width, label=rec["name"], color=PALETTE[i], alpha=0.85
        )

    ax.set_xticks(x)
    ax.set_xticklabels(short, fontsize=8)
    ax.set_ylim(0, 1.12)
    ax.set_ylabel("F1 Score", fontsize=11)
    ax.set_title("Per-Class F1 Score for Each Model", fontsize=13, fontweight="bold")
    ax.legend(fontsize=8, loc="lower right")
    ax.yaxis.grid(True, linestyle="--", alpha=0.6)
    ax.set_axisbelow(True)
    plt.tight_layout()
    _save("per_class_f1.png", fig)


def plot_inference_time(records: list[dict], y_test: np.ndarray) -> None:
    """Plot total and per-sample inference times for all models.

    Args:
        records: List of model record dictionaries from ``_load_models``.
        y_test: Ground-truth integer labels for the test set.
    """
    names = [r["name"] for r in records]
    times = [r["infer_time"] for r in records]
    n_test = len(y_test)
    ms_per_sample = [t / n_test * 1000 for t in times]

    fig, axes = plt.subplots(1, 2, figsize=(13, 5))

    bars = axes[0].bar(names, times, color=PALETTE[: len(names)])
    axes[0].set_ylabel("Total time (s)", fontsize=11)
    axes[0].set_title("Total Inference Time", fontsize=12, fontweight="bold")
    axes[0].set_xticklabels(names, rotation=30, ha="right", fontsize=9)
    axes[0].yaxis.grid(True, linestyle="--", alpha=0.6)
    axes[0].set_axisbelow(True)
    for bar, val in zip(bars, times):
        axes[0].text(
            bar.get_x() + bar.get_width() / 2,
            bar.get_height() + 0.01,
            f"{val:.2f}s",
            ha="center",
            va="bottom",
            fontsize=8,
        )

    bars2 = axes[1].bar(names, ms_per_sample, color=PALETTE[: len(names)])
    axes[1].set_ylabel("ms / sample", fontsize=11)
    axes[1].set_title("Inference Time per Sample", fontsize=12, fontweight="bold")
    axes[1].set_xticklabels(names, rotation=30, ha="right", fontsize=9)
    axes[1].yaxis.grid(True, linestyle="--", alpha=0.6)
    axes[1].set_axisbelow(True)
    for bar, val in zip(bars2, ms_per_sample):
        axes[1].text(
            bar.get_x() + bar.get_width() / 2,
            bar.get_height() + 0.001,
            f"{val:.2f}",
            ha="center",
            va="bottom",
            fontsize=8,
        )

    plt.tight_layout()
    _save("inference_time.png", fig)


def plot_training_time(training_results: list[dict]) -> None:
    """Plot training time per model as a bar chart.

    Args:
        training_results: List of dictionaries from ``training_results.json``,
            each with ``model`` and ``train_time_s`` keys.
    """
    if not training_results:
        return
    names = [r["model"] for r in training_results]
    times = [r["train_time_s"] for r in training_results]

    fig, ax = plt.subplots(figsize=(10, 5))
    bars = ax.bar(names, times, color=PALETTE[: len(names)])
    ax.set_ylabel("Training time (s)", fontsize=11)
    ax.set_title("Training Time per Model", fontsize=13, fontweight="bold")
    ax.set_xticklabels(names, rotation=25, ha="right", fontsize=9)
    ax.yaxis.grid(True, linestyle="--", alpha=0.6)
    ax.set_axisbelow(True)
    for bar, val in zip(bars, times):
        ax.text(
            bar.get_x() + bar.get_width() / 2,
            bar.get_height() + 0.5,
            f"{val:.1f}s",
            ha="center",
            va="bottom",
            fontsize=8,
        )
    plt.tight_layout()
    _save("training_time.png", fig)


def plot_accuracy_vs_time(
    records: list[dict], y_test: np.ndarray, training_results: list[dict]
) -> None:
    """Scatter plot of accuracy vs training time, bubble-sized by inference time.

    Args:
        records: List of model record dictionaries from ``_load_models``.
        y_test: Ground-truth integer labels for the test set.
        training_results: List of dictionaries from ``training_results.json``.
    """
    train_map = {r["model"]: r["train_time_s"] for r in training_results}
    names = [r["name"] for r in records]
    accuracies = [accuracy_score(y_test, r["y_pred"]) for r in records]
    infer_times = [r["infer_time"] for r in records]
    train_times = [train_map.get(n, 0) for n in names]

    fig, ax = plt.subplots(figsize=(9, 6))
    ax.scatter(
        train_times,
        accuracies,
        s=[t * 500 + 50 for t in infer_times],
        c=range(len(names)),
        cmap="tab10",
        alpha=0.85,
        edgecolors="k",
        lw=0.8,
    )
    for name, tt, acc in zip(names, train_times, accuracies):
        ax.annotate(name, (tt, acc), textcoords="offset points", xytext=(6, 4), fontsize=8)

    ax.set_xlabel("Training time (s)", fontsize=11)
    ax.set_ylabel("Test accuracy", fontsize=11)
    ax.set_title(
        "Accuracy vs Training Time\n(bubble size proportional to inference time)",
        fontsize=12,
        fontweight="bold",
    )
    ax.yaxis.grid(True, linestyle="--", alpha=0.5)
    ax.xaxis.grid(True, linestyle="--", alpha=0.5)
    plt.tight_layout()
    _save("accuracy_vs_time.png", fig)


def plot_class_distribution(y_test: np.ndarray) -> None:
    """Plot test-set class distribution as a bar chart.

    Args:
        y_test: Ground-truth integer labels for the test set.
    """
    counts = np.bincount(y_test, minlength=N_CLASSES)
    fig, ax = plt.subplots(figsize=(10, 4))
    ax.bar(CLASSES, counts, color=sns.color_palette("tab10", N_CLASSES))
    ax.set_xticklabels(CLASSES, rotation=30, ha="right", fontsize=9)
    ax.set_ylabel("Samples", fontsize=11)
    ax.set_title("Test-set Class Distribution", fontsize=12, fontweight="bold")
    ax.yaxis.grid(True, linestyle="--", alpha=0.6)
    ax.set_axisbelow(True)
    plt.tight_layout()
    _save("class_distribution.png", fig)


def print_classification_reports(records: list[dict], y_test: np.ndarray) -> None:
    """Write per-model classification reports to a text file and log them.

    Args:
        records: List of model record dictionaries from ``_load_models``.
        y_test: Ground-truth integer labels for the test set.
    """
    report_path = PLOTS_DIR / "classification_reports.txt"
    with open(report_path, "w") as f:
        for rec in records:
            header = f'\n{"="*60}\n{rec["name"]}\n{"="*60}\n'
            f.write(header)
            logger.info(header)
            report = classification_report(
                y_test, rec["y_pred"], target_names=CLASSES, zero_division=0
            )
            f.write(report + "\n")
            logger.info(report)
    logger.info("\nClassification reports saved to %s", report_path)


def _save(filename: str, fig: plt.Figure) -> None:
    """Save a matplotlib figure to the plots directory and close it.

    Args:
        filename: Output file name (e.g. ``'confusion_matrices.png'``).
        fig: Matplotlib figure to save.
    """
    path = PLOTS_DIR / filename
    fig.savefig(path, dpi=150, bbox_inches="tight")
    plt.close(fig)
    logger.info("  Saved: %s", path)


# Main


def argument_parser():
    """Build and return the CLI argument parser for the evaluation script.

    Returns:
        Parsed argument namespace with at least a ``type`` attribute.
    """
    import argparse

    parser = argparse.ArgumentParser(description="Choose model type for evaluation.")
    parser.add_argument(
        "--type",
        choices=["underhang", "overhang"],
        default="underhang",
        help="Bearing type to evaluate (default: underhang)",
    )
    parser.add_argument(
        "--model",
        choices=["machine-learning", "deep-learning", "both"],
        default="both",
        help="Model family to evaluate (default: both)",
    )
    parser.add_argument(
        "--sample-rate",
        type=int,
        default=None,
        metavar="HZ",
        help="Target sample rate in Hz (default: SAMPLE_RATE from constants). Must match training.",
    )
    return parser.parse_args()


def main_evaluation() -> None:
    """Entry point: parse arguments, load data, run inference, and save plots."""
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    args = argument_parser()

    if args.type == "underhang":
        type_str = "underhang"
    elif args.type == "overhang":
        type_str = "overhang"
    else:
        raise ValueError(f"Invalid type: {args.type}")

    global PLOTS_DIR
    PLOTS_DIR = PLOTS_DIR / type_str
    os.makedirs(PLOTS_DIR, exist_ok=True)

    global SAVED_MODELS_DIR
    SAVED_MODELS_DIR = SAVED_MODELS_DIR / type_str

    model_family = args.model
    sample_rate = args.sample_rate if args.sample_rate is not None else SAMPLE_RATE
    logger.info("--> Model family  : %s", model_family)
    logger.info("--> Sample rate   : %d Hz (source: %d Hz)", sample_rate, SOURCE_SAMPLE_RATE)

    data = _load_data(type_str=type_str, sample_rate=sample_rate)
    y_test = data["y_test"]

    training_results: list[dict] = []
    summary_path = SAVED_MODELS_DIR / "training_results.json"
    if summary_path.exists():
        with open(summary_path) as f:
            training_results = json.load(f)

    logger.info("\nLoading models and running inference...")
    records = _load_models(data, model_family=model_family)

    if not records:
        logger.info("No saved models found. Run training.py first.")
        return

    logger.info("\nGenerating plots...")

    plot_class_distribution(y_test)
    plot_confusion_matrices(records, y_test)
    plot_roc_curves(records, y_test)
    plot_precision_recall_curves(records, y_test)
    plot_metrics_comparison(records, y_test)
    plot_per_class_f1(records, y_test)
    plot_inference_time(records, y_test)

    if training_results:
        plot_training_time(training_results)
        plot_accuracy_vs_time(records, y_test, training_results)

    print_classification_reports(records, y_test)

    logger.info("\n=== Evaluation complete ===")
    logger.info("All plots saved to %s", PLOTS_DIR)


if __name__ == "__main__":
    main_evaluation()
