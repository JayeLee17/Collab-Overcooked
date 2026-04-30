#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"

MODEL_NAME="${MODEL_NAME:-/path/to/Qwen3.5-0.8B}"
OUTPUT_DIR="${OUTPUT_DIR:-$ROOT_DIR/runs/scheduler_round1_qwen35_08b}"

python "$ROOT_DIR/scripts/sft/train_qwen_sft.py" \
  --train-data "$ROOT_DIR/results/scheduler_sft_collection/datasets/round1/train_scheduler_round1.jsonl" \
  --eval-data "$ROOT_DIR/results/scheduler_sft_collection/datasets/round1/dev_scheduler_round1.jsonl" \
  --test-data "$ROOT_DIR/results/scheduler_sft_collection/datasets/round1/test_scheduler_round1.jsonl" \
  --model-name "$MODEL_NAME" \
  --output-dir "$OUTPUT_DIR" \
  --dataset-mode single \
  --single-target-name Scheduler \
  --epochs 3 \
  --per-device-train-batch-size 1 \
  --per-device-eval-batch-size 1 \
  --gradient-accumulation-steps 16 \
  --learning-rate 2e-5 \
  --max-length 2048 \
  --use-lora \
  --lora-r 16 \
  --lora-alpha 32 \
  --lora-dropout 0.05 \
  --bf16 \
  --plot-metrics
