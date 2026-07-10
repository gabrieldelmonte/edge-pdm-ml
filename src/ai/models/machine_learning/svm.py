'''
Support Vector Machine classifier model.
'''

from sklearn.svm import SVC
from .base_ml_model import BaseMLModel


class SVMModel(BaseMLModel):
    '''
    Support Vector Machine classifier.
    '''

    def __init__(
        self,
        C: float = 10.0,
        kernel: str = 'rbf',
        gamma: str = 'scale',
        probability: bool = True,
        random_state: int = 42,
        class_weight = 'balanced',
        **kwargs,
    ):
        self._clf = SVC(
            C = C,
            kernel = kernel,
            gamma = gamma,
            probability = probability,
            random_state = random_state,
            class_weight = class_weight,
            **kwargs,
        )

    @property
    def name(self) -> str:
        return 'SVM'
