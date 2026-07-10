'''
Vanilla Recurrent Neural Network for vibration fault classification.
'''

from __future__ import annotations

import torch
import torch.nn as nn

from .base_dl_model import BaseDLModel


class _RNNNet(nn.Module):
    def __init__(self, n_channels: int, n_classes: int, hidden_size: int = 128, num_layers: int = 2):
        super().__init__()
        self.rnn = nn.RNN(
            input_size = n_channels,
            hidden_size = hidden_size,
            num_layers = num_layers,
            batch_first = True,
            nonlinearity = 'tanh',
            dropout = 0.3,
        )
        self.classifier = nn.Sequential(
            nn.Linear(hidden_size, 128),
            nn.ReLU(),
            nn.Dropout(0.5),
            nn.Linear(128, n_classes),
        )

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        # x: (B, window_size, n_channels)
        out, _ = self.rnn(x)
        last = out[:, -1, :]
        return self.classifier(last)


class RNNModel(BaseDLModel):
    '''
    Vanilla RNN fault classifier.
    '''

    def _build_network(self) -> nn.Module:
        return _RNNNet(self.n_channels, self.n_classes)

    @property
    def name(self) -> str:
        return 'RNN'
