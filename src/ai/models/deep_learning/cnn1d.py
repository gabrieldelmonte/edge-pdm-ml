'''
1-D Convolutional Neural Network for vibration fault classification.
'''

from __future__ import annotations

import numpy as np
import torch
import torch.nn as nn

from .base_dl_model import BaseDLModel


class _CNN1DNet(nn.Module):
    '''
    Four-block 1-D CNN followed by a two-layer classifier head.
    '''

    def __init__(self, n_channels: int, n_classes: int):
        super().__init__()
        self.features = nn.Sequential(
            # Block 1
            nn.Conv1d(n_channels, 32, kernel_size = 7, padding = 3),
            nn.BatchNorm1d(32),
            nn.ReLU(),

            # Block 2
            nn.Conv1d(32, 64, kernel_size = 5, padding = 2),
            nn.BatchNorm1d(64),
            nn.ReLU(),
            nn.MaxPool1d(kernel_size = 4),

            # Block 3
            nn.Conv1d(64, 128, kernel_size = 3, padding = 1),
            nn.BatchNorm1d(128),
            nn.ReLU(),
            
            # Block 4
            nn.Conv1d(128, 256, kernel_size = 3, padding = 1),
            nn.BatchNorm1d(256),
            nn.ReLU(),
            nn.MaxPool1d(kernel_size = 4),
        )
        self.global_pool = nn.AdaptiveAvgPool1d(1)
        self.classifier = nn.Sequential(
            nn.Flatten(),
            nn.Linear(256, 128),
            nn.ReLU(),
            nn.Dropout(0.5),
            nn.Linear(128, n_classes),
        )

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        # x: (B, n_channels, window_size)
        x = self.features(x)
        x = self.global_pool(x)
        return self.classifier(x)


class CNN1DModel(BaseDLModel):
    '''
    1-D CNN fault classifier.
    '''

    def _build_network(self) -> nn.Module:
        return _CNN1DNet(self.n_channels, self.n_classes)

    @property
    def name(self) -> str:
        return 'CNN1D'

    # CNN1D expects (B, C, L) - override the tensor prep
    def _prepare_input(self, X: np.ndarray) -> torch.Tensor:
        # X: (N, window_size, n_channels) -> (N, n_channels, window_size)
        return torch.from_numpy(X.astype(np.float32)).permute(0, 2, 1)
