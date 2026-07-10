'''
EXtreme Gradient Boosting (XGBoost) classifier model.
'''

from sklearn.utils.class_weight import compute_sample_weight
from xgboost import XGBClassifier
from .base_ml_model import BaseMLModel


class XGBoostModel(BaseMLModel):
    '''
    XGBoost classifier.
    '''

    def __init__(
        self,
        n_estimators: int = 300,
        max_depth: int = 6,
        learning_rate: float = 0.1,
        subsample: float = 0.8,
        colsample_bytree: float = 0.8,
        use_label_encoder: bool = False,
        eval_metric: str = 'mlogloss',
        n_jobs: int = -1,
        random_state: int = 42,
        **kwargs,
    ):
        self._clf = XGBClassifier(
            n_estimators = n_estimators,
            max_depth = max_depth,
            learning_rate = learning_rate,
            subsample = subsample,
            colsample_bytree = colsample_bytree,
            eval_metric = eval_metric,
            n_jobs = n_jobs,
            random_state = random_state,
            verbosity = 0,
            device = 'cpu',
            **kwargs,
        )

    def train(self, X_train, y_train, X_val = None, y_val = None):
        # XGBClassifier has no ``class_weight`` param, so counter the class
        # imbalance with per-sample weights (inverse class frequency).
        sample_weight = compute_sample_weight(class_weight = 'balanced', y = y_train)
        self._clf.fit(X_train, y_train, sample_weight = sample_weight)

    @property
    def name(self) -> str:
        return 'XGBoost'
