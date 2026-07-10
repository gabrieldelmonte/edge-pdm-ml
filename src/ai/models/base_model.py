"""Common interface for all fault-detection models."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Optional

import numpy as np


class BaseModel(ABC):
    """Abstract base class for every model in the comparison study.

    All models receive numpy arrays and expose a unified API so that the
    training and evaluation pipelines can treat them identically.

    Machine-learning models expect *feature* arrays (n_samples, n_features).
    Deep-learning models expect *raw window* arrays
    (n_samples, window_size, n_channels).
    """

    @property
    @abstractmethod
    def name(self) -> str:
        """Human-readable model name.

        Returns:
            A short descriptive string used in logging and result summaries.
        """

    @abstractmethod
    def train(
        self,
        X_train: np.ndarray,
        y_train: np.ndarray,
        X_val: Optional[np.ndarray] = None,
        y_val: Optional[np.ndarray] = None,
    ) -> None:
        """Fit the model on training data.

        Args:
            X_train: Training inputs. Feature matrix for ML models; raw windows
                for DL models, shape (n_samples, window_size, n_channels).
            y_train: Integer class labels in [0, n_classes).
            X_val: Optional validation inputs with the same shape as
                ``X_train``, used by DL models for early stopping.
            y_val: Optional validation labels corresponding to ``X_val``.
        """

    @abstractmethod
    def predict(self, X: np.ndarray) -> np.ndarray:
        """Return integer class predictions.

        Args:
            X: Input array with the same shape convention as ``X_train``.

        Returns:
            Integer label array of shape (n_samples,).
        """

    @abstractmethod
    def predict_proba(self, X: np.ndarray) -> np.ndarray:
        """Return class probability estimates.

        Args:
            X: Input array with the same shape convention as ``X_train``.

        Returns:
            Float array of shape (n_samples, n_classes) where each row sums to
            approximately 1.
        """

    @abstractmethod
    def save(self, path: str) -> None:
        """Persist the model to disk.

        Args:
            path: Destination file path. The format is model-specific (e.g.
                ``.pkl`` for scikit-learn, ``.pt`` for PyTorch).
        """

    @classmethod
    @abstractmethod
    def load(cls, path: str) -> BaseModel:
        """Restore and return a model from disk.

        Args:
            path: File path previously created by ``save``.

        Returns:
            A fully initialised model instance ready for inference.
        """
