'''
Bidirectional GRU for vibration fault classification.
'''

from __future__ import annotations

import torch
import torch.nn as nn

from .base_dl_model import BaseDLModel


class _GRUNet(nn.Module):
    def __init__(self, n_channels: int, n_classes: int, hidden_size: int = 128, num_layers: int = 2):
        super().__init__()
        self.gru = nn.GRU(
            input_size = n_channels,
            hidden_size = hidden_size,
            num_layers = num_layers,
            batch_first = True,
            bidirectional = True,
            dropout = 0.3,
        )
        self.classifier = nn.Sequential(
            nn.Linear(hidden_size * 2, 128),
            nn.ReLU(),
            nn.Dropout(0.5),
            nn.Linear(128, n_classes),
        )

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        # x: (B, window_size, n_channels)
        out, _ = self.gru(x)
        last = out[:, -1, :]
        return self.classifier(last)


class GRUModel(BaseDLModel):
    '''
    Bidirectional GRU fault classifier.
    '''

    def _build_network(self) -> nn.Module:
        return _GRUNet(self.n_channels, self.n_classes)

    @property
    def name(self) -> str:
        return 'GRU'
