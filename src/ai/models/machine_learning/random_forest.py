'''
Random Forest classifier model.
'''

from sklearn.ensemble import RandomForestClassifier
from .base_ml_model import BaseMLModel


class RandomForestModel(BaseMLModel):
    '''
    Random Forest classifier.
    '''

    def __init__(
        self,
        n_estimators: int = 200,
        max_depth = None,
        n_jobs: int = -1,
        random_state: int = 42,
        class_weight = 'balanced',
        **kwargs,
    ):
        self._clf = RandomForestClassifier(
            n_estimators = n_estimators,
            max_depth = max_depth,
            n_jobs = n_jobs,
            random_state = random_state,
            class_weight = class_weight,
            **kwargs,
        )

    @property
    def name(self) -> str:
        return 'Random Forest'
