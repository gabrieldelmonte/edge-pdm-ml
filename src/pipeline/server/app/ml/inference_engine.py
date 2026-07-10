"""Model loading and inference orchestration for all saved models."""

from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import joblib
import numpy as np

from app.ml.signal_processing import CLASSES

try:
    import torch
except Exception:  # pragma: no cover - runtime dependency guard
    torch = None  # type: ignore[assignment]


def _repo_ai_candidate() -> Path:
    """Return a best-effort local repo path for the shared AI package."""
    candidate = Path(__file__).resolve()
    for _ in range(4):
        candidate = candidate.parent
    return candidate / "ai"


def _ensure_ai_models_package_importable() -> None:
    """Ensure the training-time models package is importable for unpickling."""
    candidates = (Path("/app/ai-shared"), _repo_ai_candidate())
    for candidate in candidates:
        models_pkg = candidate / "models"
        if models_pkg.exists() and str(candidate) not in sys.path:
            sys.path.insert(0, str(candidate))


def _load_network_from_artifact(model_path: Path) -> Any:
    """Load a .pt torch artifact and return a CPU eval-mode Module."""
    if torch is None:
        raise RuntimeError("torch dependency is unavailable")

    _ensure_ai_models_package_importable()
    artifact = torch.load(model_path, map_location="cpu", weights_only=False)

    if isinstance(artifact, dict):
        model_obj = artifact.get("model")
        if model_obj is None:
            raise RuntimeError("Invalid torch artifact: expected key 'model'")
        if not isinstance(model_obj, torch.nn.Module):
            raise RuntimeError("Invalid torch artifact: key 'model' is not torch.nn.Module")
        model_obj.to(torch.device("cpu"))
        model_obj.eval()
        return model_obj

    if isinstance(artifact, torch.nn.Module):
        artifact.to(torch.device("cpu"))
        artifact.eval()
        return artifact

    raise RuntimeError(f"Unsupported torch artifact type: {type(artifact).__name__}")


@dataclass(frozen=True)
class ModelSpec:
    """Static metadata for one registered model artifact.

    Attributes:
        display_name: Human-readable name used in API responses.
        filename: Artifact filename within the bearing-type subdirectory.
        family: 'ml' for joblib pickles, 'dl' for torch artifacts.
        architecture: Torch architecture name for input reshaping (DL only).
    """

    display_name: str
    filename: str
    family: str
    architecture: str | None = None


MODEL_REGISTRY: tuple[ModelSpec, ...] = (
    ModelSpec("LightGBM", "lightgbm_model.pkl", "ml"),
    ModelSpec("Random Forest", "rf_model.pkl", "ml"),
    ModelSpec("SVM", "svm_model.pkl", "ml"),
    ModelSpec("XGBoost", "xgboost_model.pkl", "ml"),
    ModelSpec("CNN1D", "cnn1d_model.pt", "dl", architecture="cnn1d"),
    ModelSpec("GRU", "gru_model.pt", "dl", architecture="gru"),
    ModelSpec("LSTM", "lstm_model.pt", "dl", architecture="lstm"),
    ModelSpec("RNN", "rnn_model.pt", "dl", architecture="rnn"),
)


def _label_from_index(index: int) -> str:
    """Map class index to label; return 'class-{index}' for out-of-range values."""
    if 0 <= index < len(CLASSES):
        return CLASSES[index]
    return f"class-{index}"


def _safe_float(value: float) -> float:
    """Return 0.0 for NaN/Inf values; otherwise return float(value)."""
    if np.isnan(value) or np.isinf(value):
        return 0.0
    return float(value)


def _project_proba_to_global_classes(
    proba: np.ndarray,
    classes: np.ndarray | None,
) -> np.ndarray:
    """Project a probability vector onto the shared CLASSES index space."""
    projected = np.zeros(len(CLASSES), dtype=np.float64)
    flat = np.ravel(np.asarray(proba, dtype=np.float64))

    if classes is None:
        n_copy = min(len(flat), len(CLASSES))
        if n_copy > 0:
            projected[:n_copy] = flat[:n_copy]
    else:
        for local_idx, class_idx in enumerate(np.ravel(classes)):
            class_pos = int(class_idx)
            if local_idx >= len(flat):
                break
            if 0 <= class_pos < len(CLASSES):
                projected[class_pos] = flat[local_idx]

    total = float(np.sum(projected))
    if total > 0:
        projected = projected / total
    return projected


class InferenceEngine:
    """Loads model artifacts and executes inference across all models."""

    def __init__(self, model_root: str) -> None:
        """Initialize with the path to the bearing-type model directory."""
        self.model_root = Path(model_root)
        self._joblib_cache: dict[str, Any] = {}
        self._torch_cache: dict[str, tuple[Any, str]] = {}

    def list_models(self, bearing_type: str) -> list[dict[str, Any]]:
        """Return model names and file availability for a bearing type."""
        models: list[dict[str, Any]] = []
        for spec in MODEL_REGISTRY:
            path = self.model_root / bearing_type / spec.filename
            models.append({
                "model": spec.display_name,
                "filename": spec.filename,
                "family": spec.family,
                "available": path.exists(),
            })
        return models

    def predict_all(
        self,
        bearing_type: str,
        x_raw: np.ndarray,
        x_feat: np.ndarray,
    ) -> dict[str, dict[str, Any]]:
        """Run every registered model and return display_name -> result dict."""
        return {
            spec.display_name: self._predict_one(spec, bearing_type, x_raw, x_feat)
            for spec in MODEL_REGISTRY
        }

    def choose_result(
        self,
        results: dict[str, dict[str, Any]],
        selected_model: str,
    ) -> tuple[str, dict[str, Any]]:
        """Choose the response result, falling back to the first OK model."""
        selected = results.get(selected_model)
        if selected and selected.get("status") == "ok":
            return selected_model, selected

        for spec in MODEL_REGISTRY:
            candidate = results.get(spec.display_name)
            if candidate and candidate.get("status") == "ok":
                return spec.display_name, candidate

        return (
            selected_model,
            {
                "status": "error",
                "inference": "InferenceUnavailable",
                "confidence": 0.0,
                "class_index": -1,
                "error": "No model could produce a prediction",
            },
        )

    def _predict_one(
        self,
        spec: ModelSpec,
        bearing_type: str,
        x_raw: np.ndarray,
        x_feat: np.ndarray,
    ) -> dict[str, Any]:
        """Run one model and return a normalized result dict."""
        model_path = self.model_root / bearing_type / spec.filename
        if not model_path.exists():
            return {
                "status": "missing",
                "inference": "ModelMissing",
                "confidence": 0.0,
                "class_index": -1,
                "error": f"Model file not found: {model_path}",
            }

        try:
            if spec.family == "ml":
                return self._predict_ml(model_path, x_feat)
            return self._predict_dl(model_path, spec, x_raw)
        except Exception as exc:  # pragma: no cover - online robustness
            return {
                "status": "error",
                "inference": "InferenceError",
                "confidence": 0.0,
                "class_index": -1,
                "error": str(exc),
            }

    def _predict_ml(self, model_path: Path, x_feat: np.ndarray) -> dict[str, Any]:
        """Run a joblib ML model and aggregate all window probabilities."""
        cache_key = str(model_path)
        if cache_key not in self._joblib_cache:
            self._joblib_cache[cache_key] = joblib.load(model_path)
        model = self._joblib_cache[cache_key]

        features = np.asarray(x_feat)
        if features.ndim == 1:
            features = features.reshape(1, -1)
        if features.ndim != 2:
            raise RuntimeError(
                f"Invalid ML input shape {features.shape}; expected 2D (n_windows, n_features)"
            )

        if hasattr(model, "predict_proba"):
            proba_matrix = np.asarray(model.predict_proba(features), dtype=np.float64)
            if proba_matrix.ndim == 1:
                proba_matrix = proba_matrix.reshape(1, -1)
            classes = np.asarray(
                getattr(model, "classes_", np.arange(proba_matrix.shape[1])), dtype=np.int64
            )
            projected_rows = np.zeros((proba_matrix.shape[0], len(CLASSES)), dtype=np.float64)
            for row_idx, row in enumerate(proba_matrix):
                projected_rows[row_idx] = _project_proba_to_global_classes(row, classes)
            mean_proba = np.mean(projected_rows, axis=0)
        else:
            preds = np.asarray(model.predict(features), dtype=np.int64).reshape(-1)
            projected_rows = np.zeros((len(preds), len(CLASSES)), dtype=np.float64)
            valid_mask = (preds >= 0) & (preds < len(CLASSES))
            valid_indices = np.nonzero(valid_mask)[0]
            if len(valid_indices) > 0:
                projected_rows[valid_indices, preds[valid_indices]] = 1.0
            mean_proba = np.mean(projected_rows, axis=0)

        class_index = int(np.argmax(mean_proba))
        return {
            "status": "ok",
            "inference": _label_from_index(class_index),
            "confidence": _safe_float(float(np.max(mean_proba))),
            "class_index": class_index,
            "windows_evaluated": int(features.shape[0]),
            "aggregation": "mean_probability",
            "error": None,
        }

    def _predict_dl(
        self, model_path: Path, spec: ModelSpec, x_raw: np.ndarray
    ) -> dict[str, Any]:
        """Run a torch DL model and aggregate all window probabilities."""
        if torch is None:
            raise RuntimeError("torch dependency is unavailable")
        if spec.architecture is None:
            raise RuntimeError("DL model spec missing architecture")

        cache_key = str(model_path)
        if cache_key not in self._torch_cache:
            net = _load_network_from_artifact(model_path)
            self._torch_cache[cache_key] = (net, spec.architecture)

        net, architecture = self._torch_cache[cache_key]
        windows = np.asarray(x_raw)
        if windows.ndim == 2:
            windows = windows.reshape(1, windows.shape[0], windows.shape[1])
        if windows.ndim != 3:
            raise RuntimeError(
                f"Invalid DL input shape {windows.shape}; expected 3D (n_windows, window_size, n_channels)"
            )

        with torch.no_grad():
            tensor = torch.from_numpy(windows.astype(np.float32))
            if architecture == "cnn1d":
                tensor = tensor.permute(0, 2, 1)
            logits = net(tensor)
            proba_matrix = torch.softmax(logits, dim=1).cpu().numpy()

        if proba_matrix.ndim == 1:
            proba_matrix = proba_matrix.reshape(1, -1)

        projected_rows = np.zeros((proba_matrix.shape[0], len(CLASSES)), dtype=np.float64)
        for row_idx, row in enumerate(proba_matrix):
            projected_rows[row_idx] = _project_proba_to_global_classes(row, None)
        mean_proba = np.mean(projected_rows, axis=0)

        class_index = int(np.argmax(mean_proba))
        return {
            "status": "ok",
            "inference": _label_from_index(class_index),
            "confidence": _safe_float(float(np.max(mean_proba))),
            "class_index": class_index,
            "windows_evaluated": int(windows.shape[0]),
            "aggregation": "mean_probability",
            "error": None,
        }
