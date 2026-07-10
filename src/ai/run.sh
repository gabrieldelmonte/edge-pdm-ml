#!/usr/bin/env bash
# ---------------------------------------------------------------------------
# run.sh  -  Train and/or evaluate all MAFAULDA fault-detection models.
#
# Usage:
#   ./run.sh [OPTIONS]
#
# Options:
#   -m, --mode    MODE        What to run: train | eval | both  (default: both)
#   -t, --type    TYPE        Bearing type: underhang | overhang | both  (default: both)
#   -M, --model   MODEL       Model family: machine-learning | deep-learning | both  (default: both)
#   -f, --sample-rate HZ      Target sample rate in Hz for feature extraction
#                             (default: SOURCE_SAMPLE_RATE from constants.py)
#   -l, --log     LEVEL       Log verbosity: debug | info | warn | error  (default: info)
#   -c, --clean               Delete the local dataset before starting so it is
#                             re-downloaded and re-extracted from scratch
#   -h, --help                Show this help message and exit
#
# Log levels:
#   debug  - full Python tracebacks, no output suppressed
#   info   - normal progress messages  (default)
#   warn   - only warnings and errors
#   error  - only errors (stderr only)
#
# Examples:
#   ./run.sh                              # train then evaluate, info logging
#   ./run.sh --mode train                 # training only
#   ./run.sh --mode eval  --log warn      # evaluate with minimal output
#   ./run.sh --model machine-learning     # train and evaluate only ML models
#   ./run.sh --model deep-learning        # train and evaluate only DL models
#   ./run.sh --type underhang             # underhang bearing only
#   ./run.sh -m both -l debug             # everything, verbose
#   ./run.sh --clean                      # wipe and re-download the dataset, then run
# ---------------------------------------------------------------------------

set -euo pipefail

# Defaults
MODE="both"
LOG_LEVEL="info"
TYPE="both"
MODEL="both"
SAMPLE_RATE=""
CLEAN=false

# Resolve script location so the script works from any cwd
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV_ACTIVATE="$(realpath "${SCRIPT_DIR}/../../src/venv-edge-pdm-ml/bin/activate" 2>/dev/null || true)"
LOG_DIR="${SCRIPT_DIR}/logs"
DATASETS_DIR="${SCRIPT_DIR}/datasets"
DATASET_URL="https://www.kaggle.com/api/v1/datasets/download/vuxuancu/mafaulda-full"
DATASET_ZIP="${DATASETS_DIR}/mafaulda-full.zip"
DATASET_DIR="${DATASETS_DIR}/mafaulda"
TIMESTAMP="$(date '+%Y%m%d_%H%M%S')"

# Argument parsing
usage() {
    sed -n '3,33p' "${BASH_SOURCE[0]}" | sed 's/^# \?//'
    exit 0
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        -m|--mode)         MODE="${2,,}";        shift 2 ;;
        -t|--type)         TYPE="${2,,}";        shift 2 ;;
        -M|--model)        MODEL="${2,,}";       shift 2 ;;
        -f|--sample-rate)  SAMPLE_RATE="$2";     shift 2 ;;
        -c|--clean)        CLEAN=true;           shift 1 ;;
        -l|--log)          LOG_LEVEL="${2,,}";   shift 2 ;;
        -h|--help)         usage ;;
        *)  echo "[ERROR] Unknown option: $1"; usage ;;
    esac
done

case "$MODE" in
    train|eval|both) ;;
    *) echo "[ERROR] --mode must be train, eval, or both (got: '${MODE}')"; exit 1 ;;
esac

case "$TYPE" in
    underhang|overhang|both) ;;
    *) echo "[ERROR] --type must be underhang, overhang, or both (got: '${TYPE}')"; exit 1 ;;
esac

case "$MODEL" in
    machine-learning|deep-learning|both) ;;
    *) echo "[ERROR] --model must be machine-learning, deep-learning, or both (got: '${MODEL}')"; exit 1 ;;
esac

case "$LOG_LEVEL" in
    debug|info|warn|error) ;;
    *) echo "[ERROR] --log must be debug, info, warn, or error (got: '${LOG_LEVEL}')"; exit 1 ;;
esac

if [[ -n "$SAMPLE_RATE" ]]; then
    if [[ ! "$SAMPLE_RATE" =~ ^[0-9]+$ ]] || [[ "$SAMPLE_RATE" -le 0 ]]; then
        echo "[ERROR] --sample-rate must be a positive integer in Hz (got: '${SAMPLE_RATE}')"
        exit 1
    fi
fi

# Logging helpers
mkdir -p "${LOG_DIR}"
LOG_FILE="${LOG_DIR}/run_${TIMESTAMP}.log"

_ts()    { date '+%Y-%m-%d %H:%M:%S'; }
_log()   { local lvl="$1"; shift; echo "[$(_ts)] [${lvl^^}] $*" | tee -a "${LOG_FILE}"; }
log_debug() { [[ "$LOG_LEVEL" == "debug" ]] && _log debug "$@" || true; }
log_info()  { [[ "$LOG_LEVEL" =~ ^(debug|info)$ ]]  && _log info  "$@" || true; }
log_warn()  { [[ "$LOG_LEVEL" =~ ^(debug|info|warn)$ ]] && _log warn  "$@" || true; }
log_error() { _log error "$@"; }

# Redirect stdout/stderr based on log level
if [[ "$LOG_LEVEL" == "error" ]]; then
    exec 3>&1 1>/dev/null
elif [[ "$LOG_LEVEL" == "warn" ]]; then
    exec 3>&1
else
    exec 3>&1
fi

# Virtual-environment activation
_activate_venv() {
    if [[ -f "$VENV_ACTIVATE" ]]; then
        log_info "Activating virtual environment: ${VENV_ACTIVATE}"
        # shellcheck disable=SC1090
        source "$VENV_ACTIVATE"
    else
        log_warn "Virtual environment not found at ${VENV_ACTIVATE}"
        log_warn "Creating a virtual environment and installing dependencies..."

        python3 -m venv "${SCRIPT_DIR}/venv-edge-pdm-ml"
        VENV_ACTIVATE="${SCRIPT_DIR}/venv-edge-pdm-ml/bin/activate"
        # shellcheck disable=SC1090
        source "$VENV_ACTIVATE"

        python3 -m pip install --quiet uv
        python3 -m uv pip install -r "${SCRIPT_DIR}/requirements.txt"
    fi
}

# Dataset download helper
_download_dataset() {
    if [[ -d "${DATASET_DIR}" ]]; then
        log_info "Dataset directory already exists: ${DATASET_DIR}"
        if [[ -f "${DATASET_ZIP}" ]]; then
            rm -f "${DATASET_ZIP}"
            log_info "Removed dataset archive: ${DATASET_ZIP}"
        fi
        return
    fi

    mkdir -p "${DATASETS_DIR}"

    if [[ ! -f "${DATASET_ZIP}" ]]; then
        log_info "Downloading dataset to ${DATASET_ZIP}"
        if ! curl -L -o "${DATASET_ZIP}" "${DATASET_URL}"; then
            log_error "Dataset download failed from ${DATASET_URL}"
            exit 1
        fi
        log_info "Dataset download completed: ${DATASET_ZIP}"
    else
        log_info "Dataset archive already exists: ${DATASET_ZIP}"
    fi

    log_info "Extracting dataset archive into ${DATASETS_DIR}"
    if command -v unzip >/dev/null 2>&1; then
        if ! unzip -qo "${DATASET_ZIP}" -d "${DATASETS_DIR}"; then
            log_error "Dataset extraction failed using unzip"
            exit 1
        fi
    else
        if ! python -m zipfile -e "${DATASET_ZIP}" "${DATASETS_DIR}"; then
            log_error "Dataset extraction failed (unzip not found and python fallback failed)"
            exit 1
        fi
    fi

    # Some archives extract as mafaulda-full/; normalize to mafaulda/ for loaders.
    if [[ -d "${DATASETS_DIR}/mafaulda-full" && ! -d "${DATASET_DIR}" ]]; then
        mv "${DATASETS_DIR}/mafaulda-full" "${DATASET_DIR}"
    fi

    if [[ ! -d "${DATASET_DIR}" ]]; then
        log_error "Expected dataset directory not found after extraction: ${DATASET_DIR}"
        exit 1
    fi

    log_info "Dataset ready at: ${DATASET_DIR}"

    if [[ -f "${DATASET_ZIP}" ]]; then
        rm -f "${DATASET_ZIP}"
        log_info "Removed dataset archive: ${DATASET_ZIP}"
    fi
}

# Python invocation wrapper
_run_python() {
    local module="$1"
    local label="$2"
    shift 2

    log_info "Starting: ${label}"
    log_debug "Command: python -m ${module} $*"
    log_debug "Working directory: ${SCRIPT_DIR}"

    local start_ts
    start_ts=$(date +%s)

    if [[ "$LOG_LEVEL" == "debug" ]]; then
        python -m "${module}" "$@" 2>&1 | tee -a "${LOG_FILE}"
    elif [[ "$LOG_LEVEL" == "error" ]]; then
        python -m "${module}" "$@" >> "${LOG_FILE}" 2>&1
    else
        python -m "${module}" "$@" 2>&1 | tee -a "${LOG_FILE}"
    fi

    local status="${PIPESTATUS[0]}"
    local elapsed=$(( $(date +%s) - start_ts ))

    if [[ "$status" -ne 0 ]]; then
        log_error "${label} FAILED (exit ${status}) after ${elapsed}s - see ${LOG_FILE}"
        exit "$status"
    fi

    log_info "${label} completed in ${elapsed}s"
}

# Build common extra args to pass to training/evaluation scripts
_extra_args() {
    local args=("--model" "$MODEL")
    if [[ -n "$SAMPLE_RATE" ]]; then
        args+=("--sample-rate" "$SAMPLE_RATE")
    fi
    echo "${args[@]}"
}

# GPU memory allocator: reduces fragmentation when training multiple models
# sequentially on a device with limited VRAM.
export PYTORCH_ALLOC_CONF=expandable_segments:True

# Main
log_info "========================================================================"
log_info " edge-pdm-ml"
log_info " -----------"
log_info " mode=${MODE} | type=${TYPE} | model=${MODEL} | log=${LOG_LEVEL}${SAMPLE_RATE:+ | sample_rate=${SAMPLE_RATE}Hz}"
log_info "========================================================================"
log_info "Log file: ${LOG_FILE}"

if [[ "$CLEAN" == true ]]; then
    if [[ -d "${DATASET_DIR}" ]]; then
        log_info "Clean requested -- removing dataset directory: ${DATASET_DIR}"
        rm -rf "${DATASET_DIR}"
    fi
    if [[ -f "${DATASET_ZIP}" ]]; then
        log_info "Clean requested -- removing dataset archive: ${DATASET_ZIP}"
        rm -f "${DATASET_ZIP}"
    fi
fi

_download_dataset
_activate_venv

cd "${SCRIPT_DIR}"

# Collect extra args once
EXTRA=($(_extra_args))

if [[ "$MODE" == "train" || "$MODE" == "both" ]]; then
    if [[ "$TYPE" == "underhang" || "$TYPE" == "both" ]]; then
        _run_python "training.training" "Training Underhang..." --type underhang "${EXTRA[@]}"
    fi
    if [[ "$TYPE" == "overhang" || "$TYPE" == "both" ]]; then
        _run_python "training.training" "Training Overhang..." --type overhang "${EXTRA[@]}"
    fi
fi

if [[ "$MODE" == "eval" || "$MODE" == "both" ]]; then
    if [[ "$TYPE" == "underhang" || "$TYPE" == "both" ]]; then
        _run_python "evaluation.evaluation" "Evaluating Underhang..." --type underhang "${EXTRA[@]}"
    fi
    if [[ "$TYPE" == "overhang" || "$TYPE" == "both" ]]; then
        _run_python "evaluation.evaluation" "Evaluating Overhang..." --type overhang "${EXTRA[@]}"
    fi
fi

log_info "========================================================================"
log_info " All done."
