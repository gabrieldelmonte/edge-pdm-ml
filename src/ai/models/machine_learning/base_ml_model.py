'''
Shared base for scikit-learn-backed machine-learning models.
'''

import joblib
import numpy as np

from ..base_model import BaseModel


class BaseMLModel(BaseModel):
    '''
    Thin wrapper around a scikit-learn estimator.

    Subclasses only need to assign ``self._clf`` in ``__init__``.
    '''

    def train(self, X_train, y_train, X_val = None, y_val = None):
        self._clf.fit(X_train, y_train)

    def predict(self, X: np.ndarray) -> np.ndarray:
        return self._clf.predict(X)

    def predict_proba(self, X: np.ndarray) -> np.ndarray:
        return self._clf.predict_proba(X)

    def save(self, path: str) -> None:
        joblib.dump(self._clf, path)

    @classmethod
    def load(cls, path: str) -> 'BaseMLModel':
        obj = cls.__new__(cls)
        obj._clf = joblib.load(path)
        return obj
