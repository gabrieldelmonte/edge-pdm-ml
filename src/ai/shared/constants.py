from __future__ import annotations

SOURCE_SAMPLE_RATE: int = 50_000
SERVER_SOURCE_SAMPLE_RATE: int = 5_000  # Default source rate on server side
SAMPLE_RATE: int = 5_000
WINDOW_SIZE: int = 256
WINDOW_STRIDE: int = 256
N_CHANNELS: int = 3
N_CLASSES: int = 10
CLASSES: list[str] = [
    "normal",
    "horizontal-misalignment",
    "vertical-misalignment",
    "imbalance",
    "overhang-ball_fault",
    "overhang-cage_fault",
    "overhang-outer_race",
    "underhang-ball_fault",
    "underhang-cage_fault",
    "underhang-outer_race",
]
FREQ_ANALYSIS_MAX_HZ: float = 5_000.0
FREQ_ANALYSIS_STEP_HZ: float = 250.0
N_FREQ_BANDS: int = 5
