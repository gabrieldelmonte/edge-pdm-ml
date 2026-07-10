'''
Analyze MAFAULDA dataset in the frequency domain across all files.
Plots the mean frequency spectrum (with ±1 standard deviation) for each class.
'''

from collections import defaultdict
from pathlib import Path

import numpy as np
import pandas as pd
from tqdm import tqdm

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt

# Import constants and helpers from your existing loader
from mafaulda_loader import DATASET_ROOT, CLASSES, SAMPLE_RATE, _get_class_label

def plot_all_frequency_spectra() -> None:
    '''
    Iterates through all CSV files in the MAFAULDA dataset, groups them by class,
    computes the Fast Fourier Transform (FFT) on the radial channel (col 5) for 
    each file, and plots the mean frequency spectra across all files for each class.
    
    The resulting plot is saved to disk, avoiding interactive GUI dependencies.
    '''
    # 1. Group all CSV files by class
    class_files = defaultdict(list)
    for csv_path in DATASET_ROOT.rglob('*.csv'):
        label = _get_class_label(csv_path, DATASET_ROOT)
        if label is not None:
            class_files[label].append(csv_path)

    if not class_files:
        print('No CSV files found in the dataset directory.')
        return

    # 2. Setup matplotlib figure with subplots (10 rows, 2 columns)
    fig, axes = plt.subplots(10, 2, figsize = (16, 20))
    axes = axes.flatten()

    print('Processing files and computing FFTs...')
    
    for idx, cls in enumerate(CLASSES):
        ax = axes[idx]
        files = class_files.get(cls, [])
        
        if not files:
            ax.set_title(f'{cls} (No Data found)')
            ax.axis('off')
            continue

        print(f'Processing {len(files)} files for class: {cls}')
        
        all_fft_mags_underhang = []
        all_fft_mags_overhang = []
        freqs = None
        
        for csv_file in tqdm(files, desc = cls, leave = False):
            try:
                # 3. Read the underhang-radial channel (Column 2)
                data = pd.read_csv(csv_file, header = None, usecols = [2]).values.flatten()

                # Take 50,000 samples (1 second of data) for a clean 1Hz resolution FFT
                # Ensure we only process if we have the full 1-second interval to keep shapes consistent
                if len(data) < SAMPLE_RATE:
                    continue

                sig_underhang = data[:SAMPLE_RATE]

                # 4. Read the overhang-radial channel (Column 5)
                data = pd.read_csv(csv_file, header = None, usecols = [5]).values.flatten()
                
                # Take 50,000 samples (1 second of data) for a clean 1Hz resolution FFT
                # Ensure we only process if we have the full 1-second interval to keep shapes consistent
                if len(data) < SAMPLE_RATE:
                    continue
                    
                sig_overhang = data[:SAMPLE_RATE]
                
                # 5. Remove DC offset (mean) so the 0 Hz peak doesn't dominate the plot
                sig_underhang = sig_underhang - np.mean(sig_underhang)
                sig_overhang = sig_overhang - np.mean(sig_overhang)

                # 6. Compute the Fast Fourier Transform (FFT)
                n = len(sig_underhang)
                if freqs is None:
                    freqs = np.fft.rfftfreq(n, d = 1.0/SAMPLE_RATE)
                    
                fft_mag_underhang = np.abs(np.fft.rfft(sig_underhang))
                fft_mag_overhang = np.abs(np.fft.rfft(sig_overhang))
                
                # Normalize magnitude for easier visual comparison across files
                max_mag_underhang = np.max(fft_mag_underhang)
                max_mag_overhang = np.max(fft_mag_overhang)
                if max_mag_underhang > 0:
                    fft_mag_underhang = fft_mag_underhang / max_mag_underhang
                if max_mag_overhang > 0:
                    fft_mag_overhang = fft_mag_overhang / max_mag_overhang

                all_fft_mags_underhang.append(fft_mag_underhang)
                all_fft_mags_overhang.append(fft_mag_overhang)
            except Exception:
                # Skip files that might be corrupted or unreadable
                pass
                
        if not all_fft_mags_underhang:
            ax.set_title(f'{cls} (Failed to process files)')
            ax.axis('off')
            continue
            
        # Convert to numpy array for statistics along the file axis (axis=0)
        all_fft_mags_underhang = np.array(all_fft_mags_underhang)
        all_fft_mags_overhang = np.array(all_fft_mags_overhang)
        
        # Calculate mean and standard deviation across all files
        mean_fft_underhang = np.mean(all_fft_mags_underhang, axis = 0)
        std_fft_underhang = np.std(all_fft_mags_underhang, axis = 0)
        mean_fft_overhang = np.mean(all_fft_mags_overhang, axis = 0)
        std_fft_overhang = np.std(all_fft_mags_overhang, axis = 0)

        # 6. Subplot the underhang spectrum with shaded std deviation on the left
        ax = axes[idx * 2]  # Left column for underhang
        
        ax.plot(freqs, mean_fft_underhang, label = 'Underhang Radial', color = 'blue')
        ax.fill_between(freqs, mean_fft_underhang - std_fft_underhang, mean_fft_underhang + std_fft_underhang, color = 'blue', alpha = 0.3)

        ax.set_title(cls)
        ax.set_xlabel('Frequency (Hz)')
        ax.set_xlim(0, 10000) 
        ax.set_ylabel('Normalized Magnitude')
        ax.grid(True, linestyle = '--', alpha = 0.6)
        ax.legend(loc = 'upper right')


        # 7. Sublot the overhang spectrum with shaded std deviation on the right
        ax = axes[idx * 2 + 1]  # Right column for overhang

        ax.plot(freqs, mean_fft_overhang, label = 'Overhang Radial', color = 'orange')
        ax.fill_between(freqs, mean_fft_overhang - std_fft_overhang, mean_fft_overhang + std_fft_overhang, color = 'orange', alpha = 0.3)

        ax.set_title(cls)
        ax.set_xlabel('Frequency (Hz)')
        ax.set_xlim(0, 10000) 
        ax.set_ylabel('Normalized Magnitude')
        ax.grid(True, linestyle = '--', alpha = 0.6)
        ax.legend(loc = 'upper right')

    plt.tight_layout()
    
    # Save the plot to a file
    output_path = Path(__file__).parent / 'mafaulda_frequency_analysis.png'
    plt.savefig(output_path, dpi = 150)
    print(f'\nPlot successfully saved to: {output_path}')
    plt.close()

if __name__ == '__main__':
    plot_all_frequency_spectra()
