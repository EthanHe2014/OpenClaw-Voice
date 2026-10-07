#!/bin/bash
# run_wake_train.sh — durable launcher for the hey_spark training run.
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

mkdir -p "$WORK/.mplcache" "$WORK/output"

write_status() {
  printf '{"stage":"%s","started":%s,"ended":%s,"exit":%s}\n' "$1" "$2" "$(date +%s)" "$3" > "$STATUS"
}

cd "$WORK"

T=$(date +%s)
echo "=== START $(date) ===" | tee -a "$LOG"

echo "=== STEP 1: generate clips $(date) ===" | tee -a "$LOG"
"$PY" -u "$OWW/train.py" --training_config "$WORK/hey_spark.yaml" --generate_clips >> "$LOG" 2>&1
E1=$?; write_status "generate_clips" "$T" "$E1"
[ $E1 -ne 0 ] && { echo "STEP1_FAILED $E1" | tee -a "$LOG"; exit $E1; }

echo "=== STEP 2: augment clips $(date) ===" | tee -a "$LOG"
"$PY" -u "$OWW/train.py" --training_config "$WORK/hey_spark.yaml" --augment_clips --overwrite >> "$LOG" 2>&1
E2=$?; write_status "augment_clips" "$T" "$E2"
[ $E2 -ne 0 ] && { echo "STEP2_FAILED $E2" | tee -a "$LOG"; exit $E2; }

echo "=== STEP 3: train model $(date) ===" | tee -a "$LOG"
"$PY" -u "$OWW/train.py" --training_config "$WORK/hey_spark.yaml" --train_model >> "$LOG" 2>&1
E3=$?; write_status "train_model" "$T" "$E3"
[ $E3 -ne 0 ] && { echo "STEP3_FAILED $E3" | tee -a "$LOG"; exit $E3; }

echo "=== DONE $(date) ===" | tee -a "$LOG"
write_status "done" "$T" 0
ls -la "$WORK/output/hey_spark/" >> "$LOG" 2>&1
