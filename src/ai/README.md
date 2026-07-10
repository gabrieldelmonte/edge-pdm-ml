# AI Training & Evaluation

Offline training and evaluation of the eight classifiers that back the inference server, using the public **MAFAULDA** (Machinery Fault Database) vibration dataset. Two independent model families are produced — one per accelerometer mounting position — each comparing four classical machine-learning models against four deep-learning models on the same 10-class fault taxonomy.

## Dataset

MAFAULDA records a rotating-machinery fault simulator at 50,000 Hz across ten operating conditions: normal operation, horizontal and vertical shaft misalignment, rotor imbalance, and three bearing fault types (ball, cage, outer race) at each of two accelerometer mounting positions (`overhang`, `underhang`). Each recording carries a tachometer channel plus two tri-axial accelerometers; only one accelerometer's three axes are used per model family, and the tachometer is never fed to a model.

The dataset is strongly imbalanced at the recording level — the `normal` class has roughly 3–4× fewer recordings than any single fault subclass — so every model in this pipeline counters that imbalance with class weighting (see [Models](#models)) rather than relying on raw recording counts.

## Preprocessing

1. **Downsample** the raw 50 kHz signal to a working sample rate via polyphase resampling (`scipy.signal.resample_poly`); production models are trained at **1,600 Hz**.
2. **Window** each recording into non-overlapping 256-sample frames (`WINDOW_SIZE = WINDOW_STRIDE = 256`).
3. **Extract features** for the classical models, or keep the raw `(256, 3)` window for the deep-learning models.
4. **Split** at the *recording* level, not the window level (see [Avoiding data leakage](#avoiding-data-leakage-recording-level-splitting)).
5. **Normalize** — fit exclusively on the training split, then applied to validation/test — `StandardScaler` over the 57-dim feature vector for classical models, channel-wise `StandardScaler` over the raw window for deep-learning models.

### Feature set (57 features: 19 per channel × 3 channels)

| Domain | Features |
|---|---|
| Time (10) | mean, standard deviation, RMS, peak, peak-to-peak, skewness, kurtosis, crest factor, shape factor, impulse factor |
| Frequency (9) | spectral centroid, spectral variance, dominant frequency, spectral entropy, and 5 band-energy features (fractional power across 5 equal-width bands from 0 Hz to Nyquist) |

Kurtosis and crest factor are of particular interest for this problem — both are known to grow markedly as a localized bearing defect increases in severity, well before it produces a perceptible symptom to an operator.

### Avoiding data leakage: recording-level splitting

Each recording produces roughly a hundred highly correlated windows (same noise floor, same DC offset, same operating point), so splitting individual *windows* at random scatters near-duplicate samples across train/validation/test — a well-documented pitfall for windowed time-series datasets that can make a high-capacity model appear to generalize when it has in fact partly memorized per-recording characteristics.

This pipeline splits at the **recording** level instead, using `StratifiedGroupKFold` keyed on a per-recording group id: no recording spans two splits, while each class still keeps its global proportion on every side. A runtime assertion verifies the three splits share no recording id before training proceeds. The final split targets roughly 70% train / 15% validation / 15% test (`random_state = 42` throughout), and a separate 5-fold cross-validation script (`evaluation/cross_validation.py`) reports per-class precision/recall/F1 as mean ± standard deviation across folds — the more trustworthy number for a scarce class like `normal`, where a single split only tests a handful of recordings.

## Models

Two families of four models each, trained per bearing position on the same 57-dim feature vector or raw `(256, 3)` window:

| Model | Type | Key hyperparameters | Imbalance handling |
|---|---|---|---|
| Random Forest | ML | 200 trees, unbounded depth | balanced class weights |
| SVM | ML | RBF kernel, `C=10`, `gamma='scale'` | balanced class weights |
| XGBoost | ML | 300 estimators, depth 6, `lr=0.1`, subsample/colsample 0.8 | balanced sample weights |
| LightGBM | ML | 300 estimators, 63 leaves, `lr=0.05`, subsample/colsample 0.8 | balanced class weights |
| CNN1D | DL | 4 conv blocks (32→256 channels), BatchNorm, MaxPool, global average pool, dropout 0.5 | class-weighted cross-entropy |
| LSTM | DL | Bidirectional, 128 hidden units, 2 layers, dropout 0.3 | class-weighted cross-entropy |
| GRU | DL | Same topology as LSTM, GRU cells | class-weighted cross-entropy |
| RNN | DL | Unidirectional, `tanh`, 128 hidden units, 2 layers | class-weighted cross-entropy |

Deep-learning models share one training loop: Adam (`lr=1e-4`), `ReduceLROnPlateau` (factor 0.5, patience 5 epochs), gradient-norm clipping at 1.0, batch size 80, up to 200 epochs with early stopping after 15 epochs without validation-loss improvement, restoring the best checkpoint at the end.

## Results (held-out test split)

**Underhang:**

| Model | Accuracy | Macro F1 |
|---|---|---|
| Random Forest | 0.83 | 0.82 |
| SVM | 0.69 | 0.62 |
| XGBoost | 0.86 | 0.85 |
| LightGBM | 0.87 | 0.86 |
| CNN1D | 1.00 | 1.00 |
| LSTM | 0.98 | 0.97 |
| GRU | 0.99 | 0.98 |
| RNN | 0.83 | 0.75 |

**Overhang:**

| Model | Accuracy | Macro F1 |
|---|---|---|
| Random Forest | 0.79 | 0.76 |
| SVM | 0.77 | 0.73 |
| XGBoost | 0.81 | 0.79 |
| LightGBM | 0.82 | 0.80 |
| CNN1D | 1.00 | 1.00 |
| LSTM | 0.98 | 0.98 |
| GRU | 0.99 | 0.98 |
| RNN | 0.91 | 0.85 |

Classical models trained on hand-crafted features land in a consistent 69–87% range across both positions; the recurrent and convolutional models operating on raw signal score markedly higher, consistent with their ability to learn temporal patterns the 57 scalar features don't fully capture. A near-perfect score across all ten classes on both independently trained families is nonetheless worth treating with some caution — very high accuracy in a windowed-signal classification setting warrants checking that the experimental protocol (recording-disjoint splitting, feature/scaler fitting on the training split only) was actually followed, rather than taking the number at face value. This project's recording-level split (above) is precisely that check.

## Running

The `run.sh` wrapper handles dataset download/extraction, virtual environment bootstrap, and chains training then evaluation:

```bash
./run.sh                            # train + evaluate, both bearing positions, all 8 models
./run.sh --mode train               # training only
./run.sh --mode eval --log warn     # evaluation only, minimal logs
./run.sh --model machine-learning   # classical models only (fast)
./run.sh --type underhang           # one bearing position only
./run.sh --sample-rate 1600         # override the working sample rate
./run.sh --clean                    # wipe and re-download the dataset first
```

Or invoke the modules directly from `src/ai/`:

```bash
python -m training.training     --type overhang --model both
python -m evaluation.evaluation --type overhang --model both
python -m evaluation.cross_validation --type overhang --model machine-learning
```

`--sample-rate`, `--type`, and any file-count cap must match between a training run and its corresponding evaluation run, or the extracted features/windows won't line up with the saved model.

## Outputs

Each training run writes to `saved_models/<bearing_type>/`: one artifact per model (`.pkl` via `joblib` for classical models, `.pt` via `torch.save` for deep-learning models, saved with both the full module and its state dict) and a `training_results.json` recording each model's validation accuracy, wall-clock training time, and the exact git commit used — since validation accuracy is a *selection* metric (also used for early stopping), the headline numbers to report are always the held-out test-set results above, not `training_results.json`.

Each evaluation run writes to `evaluation/plots/<bearing_type>/`: per-model classification reports, normalized confusion matrices, ROC and precision-recall curves, and comparative plots of accuracy, F1, training time, and inference time across all eight models.

Both `saved_models/` and `evaluation/plots/` are generated locally and left untracked (see `.gitignore`) — the model artifacts alone run into the hundreds of megabytes per bearing type, well past what belongs in version control. Run `./run.sh` once to populate both before starting the inference server (below).
