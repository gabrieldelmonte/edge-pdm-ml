'''
Shared PyTorch training loop for all deep-learning models.

Subclasses must implement ``_build_network()`` which returns a ``nn.Module``
that accepts input ``(batch, n_channels, window_size)`` for CNN1D or
``(batch, window_size, n_channels)`` for recurrent architectures, and outputs
logits of shape ``(batch, n_classes)``.
'''

from __future__ import annotations

import os
from abc import abstractmethod
from typing import Optional

import numpy as np
import torch
import torch.nn as nn
from torch.utils.data import DataLoader, TensorDataset
from tqdm import tqdm

from ..base_model import BaseModel


class BaseDLModel(BaseModel):
    '''
    Base class for PyTorch deep-learning fault-detection models.

    Subclasses set ``self._net`` (an ``nn.Module``) inside ``__init__``.
    '''

    DEFAULT_BATCH_SIZE: int = 128
    DEFAULT_EPOCHS: int = 100
    DEFAULT_LR: float = 1e-3
    DEFAULT_PATIENCE: int = 10

    def __init__(
        self,
        n_classes: int,
        window_size: int,
        n_channels: int,
        batch_size: int = DEFAULT_BATCH_SIZE,
        epochs: int = DEFAULT_EPOCHS,
        lr: float = DEFAULT_LR,
        patience: int = DEFAULT_PATIENCE,
    ):
        self.n_classes = n_classes
        self.window_size = window_size
        self.n_channels = n_channels
        self.batch_size = batch_size
        self.epochs = epochs
        self.lr = lr
        self.patience = patience
        self.device = torch.device('cuda') if torch.cuda.is_available() else torch.device('cpu')
        self._net: nn.Module = self._build_network()
        self._net.to(self.device)

    @abstractmethod
    def _build_network(self) -> nn.Module:
        '''
        Return the untrained neural network.
        '''


    def train(
        self,
        X_train: np.ndarray,
        y_train: np.ndarray,
        X_val: Optional[np.ndarray] = None,
        y_val: Optional[np.ndarray] = None,
    ) -> None:
        '''
        Full training loop with early stopping on validation loss.
        '''
        print(f'  --> Using device: {self.device} for {self.name}', flush = True)

        # Free any allocator fragments left by a previous model before starting.
        if self.device.type == 'cuda':
            torch.cuda.empty_cache()

        self._net.train()

        train_loader = self._make_loader(X_train, y_train, shuffle = True)
        val_loader   = self._make_loader(X_val, y_val, shuffle = False) if X_val is not None else None

        optimiser  = torch.optim.Adam(self._net.parameters(), lr = self.lr)
        scheduler  = torch.optim.lr_scheduler.ReduceLROnPlateau(
            optimiser, mode = 'min', factor = 0.5, patience = 5
        )

        # Balanced class weights (inverse frequency) counter the strong class
        # imbalance - notably the scarce ``normal`` class - so the loss does not
        # collapse onto the majority faults. Robust to absent classes.
        counts = np.bincount(y_train, minlength = self.n_classes).astype(np.float64)
        class_weights = counts.sum() / (self.n_classes * np.maximum(counts, 1.0))
        weight_tensor = torch.tensor(class_weights, dtype = torch.float32, device = self.device)
        criterion = nn.CrossEntropyLoss(weight = weight_tensor)

        best_val_loss  = float('inf')
        epochs_no_improve = 0
        best_state_dict = None

        for epoch in range(1, self.epochs + 1):
            train_desc = f'Epoch {epoch:3d}/{self.epochs} [Train]'
            train_loss = self._run_epoch(train_loader, criterion, optimiser, training = True, desc = train_desc)
            status = f'Epoch {epoch:3d}/{self.epochs}  train_loss={train_loss:.4f}'

            if val_loader is not None:
                val_desc = f'Epoch {epoch:3d}/{self.epochs} [Val]  '
                val_loss = self._run_epoch(val_loader, criterion, None, training = False, desc = val_desc)
                scheduler.step(val_loss)
                status += f'  val_loss={val_loss:.4f}'

                if val_loss < best_val_loss:
                    best_val_loss = val_loss
                    epochs_no_improve = 0
                    best_state_dict = {
                        k: v.cpu().clone() for k, v in self._net.state_dict().items()
                    }
                else:
                    epochs_no_improve += 1
                    if epochs_no_improve >= self.patience:
                        print(f'{status}  (early stopping)')
                        break
            print(status)

        # Restore best weights
        if best_state_dict is not None:
            self._net.load_state_dict(best_state_dict)

    def predict(self, X: np.ndarray) -> np.ndarray:
        proba = self.predict_proba(X)
        return np.argmax(proba, axis = 1)

    def predict_proba(self, X: np.ndarray) -> np.ndarray:
        self._net.eval()
        loader = self._make_loader(X, labels = None, shuffle = False)
        all_probs: list[np.ndarray] = []

        with torch.no_grad():
            for (x_batch,) in loader:
                x_batch = x_batch.to(self.device)
                logits  = self._net(x_batch)
                probs   = torch.softmax(logits, dim = 1).cpu().numpy()
                all_probs.append(probs)

        return np.concatenate(all_probs, axis = 0)

    def save(self, path: str) -> None:
        os.makedirs(os.path.dirname(path) or '.', exist_ok = True)
        # Move to CPU before pickling so the artifact loads on any machine.
        self._net.cpu()
        torch.save(
            {
                'model':      self._net,              # full Module for inference_engine.py
                'state_dict': self._net.state_dict(), # kept for BaseDLModel.load()
                'config': {
                    'n_classes':   self.n_classes,
                    'window_size': self.window_size,
                    'n_channels':  self.n_channels,
                    'batch_size':  self.batch_size,
                    'epochs':      self.epochs,
                    'lr':          self.lr,
                    'patience':    self.patience,
                },
            },
            path,
        )
        self._net.to(self.device)

    @classmethod
    def load(cls, path: str) -> 'BaseDLModel':
        checkpoint = torch.load(path, map_location = 'cpu', weights_only = False)
        obj = cls(**checkpoint['config'])
        obj._net.load_state_dict(checkpoint['state_dict'])
        obj._net.to(obj.device)
        return obj


    # Internal helpers

    @property
    def _dataloader_num_workers(self) -> int:
        '''
        Number of CPU workers for DataLoader prefetching.

        Uses half the available cores so the GPU compute thread is not starved.
        Set to 0 to disable multiprocessing (useful for debugging).
        '''
        return min(8, max(0, (os.cpu_count() or 1) // 2))

    @property
    def _dataloader_pin_memory(self) -> bool:
        '''
        Enable pinned memory when training on GPU for faster CPU→GPU transfers.
        '''
        return self.device.type == "cuda"

    def _prepare_input(self, X: np.ndarray) -> torch.Tensor:
        '''
        Convert a (N, window_size, n_channels) numpy array to a tensor.

        Subclasses that need a different layout (e.g. CNN1D) should override.
        '''
        return torch.from_numpy(X.astype(np.float32))

    def _make_loader(
        self,
        X: np.ndarray,
        labels: Optional[np.ndarray],
        shuffle: bool,
    ) -> DataLoader:
        x_tensor = self._prepare_input(X)
        if labels is not None:
            y_tensor = torch.from_numpy(labels.astype(np.int64))
            dataset  = TensorDataset(x_tensor, y_tensor)
        else:
            dataset  = TensorDataset(x_tensor)
        num_workers = self._dataloader_num_workers
        return DataLoader(
            dataset,
            batch_size = self.batch_size,
            shuffle = shuffle,
            num_workers = num_workers,
            pin_memory = self._dataloader_pin_memory,
            persistent_workers = num_workers > 0,
        )

    def _run_epoch(
        self,
        loader: DataLoader,
        criterion: nn.Module,
        optimiser: Optional[torch.optim.Optimizer],
        training: bool,
        desc: str = '',
    ) -> float:
        total_loss = 0.0
        n_batches  = 0
        self._net.train(training)

        iterator = tqdm(loader, desc=desc, leave=False) if desc else loader

        for batch in iterator:
            x_batch, y_batch = batch[0].to(self.device), batch[1].to(self.device)
            logits = self._net(x_batch)
            loss   = criterion(logits, y_batch)

            if training and optimiser is not None:
                optimiser.zero_grad()
                loss.backward()
                nn.utils.clip_grad_norm_(self._net.parameters(), 1.0)
                optimiser.step()

            total_loss += loss.item()
            n_batches  += 1
            
            if desc:
                iterator.set_postfix(loss = f'{total_loss/n_batches:.4f}')

        return total_loss / max(n_batches, 1)
