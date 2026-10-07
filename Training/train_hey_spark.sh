#!/bin/bash
# train_hey_spark.sh - train a custom "hey spark" openWakeWord model.
# Assets are pre-downloaded. Background audio + RIRs are OPTIONAL; created empty if absent.
set -x
ROOT="/Users/Ethan/Desktop/Projects/Spark Voice"
VENV="$ROOT/.venv"
PY="$VENV/bin/python"
OWW="$VENV/lib/python3.14/site-packages/openwakeword"
WORK="$ROOT/wake_training"
export https_proxy="http://127.0.0.1:7897" http_proxy="http://127.0.0.1:7897" all_proxy="socks5://127.0.0.1:7897"
OMP_NUM_THREADS=8

echo "=== START $(date) ==="
cd "$WORK"

# Pre-downloaded assets
if [ ! -f "$WORK/piper-sample-generator/models/en_US-libritts_r-medium.pt" ]; then
  echo "MISSING voice model - aborting"; exit 1
fi

# Optional dirs: create empty so config paths resolve
mkdir -p "$WORK/mit_rirs" "$WORK/audioset"
rm -f "$WORK/audioset/background.tar.gz"

# Training config (no augmentation sources needed)
cat > "$WORK/hey_spark.yaml" <<'YAML'
target_phrase:
  - "hey spark"
model_name: "hey_spark"
n_samples: 20000
n_samples_val: 2000
tts_batch_size: 100
augmentation_rounds: 1
output_dir: "./output"
piper_sample_generator_path: "./piper-sample-generator"
rir_paths:
  - "./mit_rirs"
background_paths:
  - "./audioset"
background_paths_duplication_rate:
  - 1
false_positive_validation_data_path: ""
feature_data_files: {}
YAML

echo "=== STEP 1: generate clips $(date) ==="
"$PY" "$OWW/train.py" --training_config "$WORK/hey_spark.yaml" --generate_clips

echo "=== STEP 2: augment clips $(date) ==="
"$PY" "$OWW/train.py" --training_config "$WORK/hey_spark.yaml" --augment_clips --overwrite

echo "=== STEP 3: train model $(date) ==="
"$PY" "$OWW/train.py" --training_config "$WORK/hey_spark.yaml" --train_model

echo "=== DONE $(date) ==="
ls -la "$WORK/output/hey_spark/" 2>&1
