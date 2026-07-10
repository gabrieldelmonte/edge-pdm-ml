'''
LightGBM classifier model.
'''

import lightgbm as lgb
from .base_ml_model import BaseMLModel


class LightGBMModel(BaseMLModel):
    '''
    LightGBM classifier.
    '''

    def __init__(
        self,
        n_estimators: int = 300,
        max_depth: int = -1,
        num_leaves: int = 63,
        learning_rate: float = 0.05,
        subsample: float = 0.8,
        colsample_bytree: float = 0.8,
        n_jobs: int = -1,
        random_state: int = 42,
        verbose: int = -1,
        class_weight = 'balanced',
        **kwargs,
    ):
        self._clf = lgb.LGBMClassifier(
            n_estimators = n_estimators,
            max_depth = max_depth,
            num_leaves = num_leaves,
            learning_rate = learning_rate,
            subsample = subsample,
            colsample_bytree = colsample_bytree,
            n_jobs = n_jobs,
            random_state = random_state,
            verbose = verbose,
            class_weight = class_weight,
            **kwargs,
        )

    @property
    def name(self) -> str:
        return 'LightGBM'
