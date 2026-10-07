#!/bin/bash
# train_only.sh — run ONLY the model-training step, reusing existing features.
set -x
ROOT="/Users/Ethan/Desktop/Projects/Spark Voice"
VENV="$ROOT/.venv"
PY="$VENV/bin/python"
OWW="$VENV/lib/python3.14/site-packages/openwakeword"
WORK="$ROOT/wake_training"
LOG="$ROOT/wake_train.log"
STATUS="$ROOT/wake_train_status.json"

export https_proxy="http://127.0.0.1:7897" http_proxy="http://127.0.0.1:7897" all_proxy="socks5://127.0.0.1:7897"
export OMP_NUM_THREADS=8
export MPLCONFIGDIR="$WORK/.mplcache"
mkdir -p "$WORK/.mplcache"

cd "$WORK"
echo "=== TRAIN-ONLY START $(date) ===" >> "$LOG"
printf '{"stage":"train_only","started":%s,"ended":0,"exit":0}\n' "$(date +%s)" > "$STATUS"

"$PY" -u "$OWW/train.py" --training_config "$WORK/hey_spark.yaml" --train_model >> "$LOG" 2>&1
E=$?
printf '{"stage":"train_only","started":%s,"ended":%s,"exit":%s}\n' "$(date +%s)" "$(date +%s)" "$E" > "$STATUS"
if [ $E -ne 0 ]; then echo "TRAIN_ONLY_FAILED $E" >> "$LOG"; else echo "=== TRAIN-ONLY DONE $(date) ===" >> "$LOG"; fi
ls -la "$WORK/output/hey_spark/" >> "$LOG" 2>&1
