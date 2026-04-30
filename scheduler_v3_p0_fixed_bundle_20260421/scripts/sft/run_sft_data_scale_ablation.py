#!/usr/bin/env python3
"""
Run scheduler SFT data-scale ablations with fixed val/test splits.

This script keeps the validation and test files unchanged, creates nested
deterministic train subsets from the original training split, and then:
1. evaluates the base model on the shared test set
2. fine-tunes LoRA SFT models on each train subset with identical hyperparams
3. evaluates every trained checkpoint on the same shared test set
4. writes aggregate CSV / JSON / plot artifacts for paper-ready analysis

Example
-------
python scripts/sft/run_sft_data_scale_ablation.py \
  --train-file results/scheduler_sft_collection/datasets/final_clean_v3_p0_fixed/train_scheduler_final_p0_fixed.jsonl \
  --valid-file results/scheduler_sft_collection/datasets/final_clean_v3_p0_fixed/val_scheduler_final_p0_fixed.jsonl \
  --test-file results/scheduler_sft_collection/datasets/final_clean_v3_p0_fixed/test_scheduler_final_p0_fixed.jsonl \
  --base-model Qwen/Qwen3.5-0.8B \
  --output-dir outputs/sft_ablation \
  --result-dir results \
  --seed 42
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import random
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple


DEFAULT_TRAIN = Path(
    "results/scheduler_sft_collection/datasets/final_clean_v3_p0_fixed/train_scheduler_final_p0_fixed.jsonl"
)
DEFAULT_VALID = Path(
    "results/scheduler_sft_collection/datasets/final_clean_v3_p0_fixed/val_scheduler_final_p0_fixed.jsonl"
)
DEFAULT_TEST = Path(
    "results/scheduler_sft_collection/datasets/final_clean_v3_p0_fixed/test_scheduler_final_p0_fixed.jsonl"
)
DEFAULT_TRAIN_SCRIPT = Path("scripts/sft/train_qwen_sft.py")
DEFAULT_EVAL_SCRIPT = Path("scripts/sft/eval_scheduler_round1_sft.py")
DEFAULT_SETTINGS = ["base", "train_10", "train_25", "train_50", "train_100"]
SETTING_TO_RATIO = {
    "train_10": 0.10,
    "train_25": 0.25,
    "train_50": 0.50,
    "train_100": 1.00,
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run scheduler SFT data-scale ablations.")
    parser.add_argument("--train-file", type=Path, default=DEFAULT_TRAIN)
    parser.add_argument("--valid-file", type=Path, default=DEFAULT_VALID)
    parser.add_argument("--test-file", type=Path, default=DEFAULT_TEST)
    parser.add_argument(
        "--base-model",
        required=True,
        help="Base model path or HF repo id used for both base eval and LoRA SFT.",
    )
    parser.add_argument("--output-dir", type=Path, default=Path("outputs/sft_ablation"))
    parser.add_argument("--result-dir", type=Path, default=Path("results"))
    parser.add_argument("--train-script", type=Path, default=DEFAULT_TRAIN_SCRIPT)
    parser.add_argument("--eval-script", type=Path, default=DEFAULT_EVAL_SCRIPT)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument(
        "--settings",
        nargs="*",
        default=DEFAULT_SETTINGS,
        help="Subset settings to run. Choices: base train_10 train_25 train_50 train_100",
    )
    parser.add_argument("--epochs", type=float, default=3.0)
    parser.add_argument("--per-device-train-batch-size", type=int, default=1)
    parser.add_argument("--per-device-eval-batch-size", type=int, default=1)
    parser.add_argument("--gradient-accumulation-steps", type=int, default=16)
    parser.add_argument("--learning-rate", type=float, default=5e-5)
    parser.add_argument("--warmup-ratio", type=float, default=0.03)
    parser.add_argument("--weight-decay", type=float, default=0.0)
    parser.add_argument("--max-length", type=int, default=4096)
    parser.add_argument("--lora-r", type=int, default=32)
    parser.add_argument("--lora-alpha", type=int, default=64)
    parser.add_argument("--lora-dropout", type=float, default=0.05)
    parser.add_argument("--target-name", default="Scheduler")
    parser.add_argument("--temperature", type=float, default=0.0)
    parser.add_argument("--top-p", type=float, default=0.9)
    parser.add_argument("--max-new-tokens", type=int, default=256)
    parser.add_argument("--device", choices=["cuda", "cpu"], default="cuda")
    parser.add_argument("--bf16", action="store_true")
    parser.add_argument("--fp16", action="store_true")
    parser.add_argument("--gradient-checkpointing", action="store_true")
    return parser.parse_args()


def load_jsonl(path: Path) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            rows.append(json.loads(line))
    return rows


def write_jsonl(path: Path, rows: Iterable[Dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")


def validate_settings(settings: Sequence[str]) -> List[str]:
    normalized: List[str] = []
    valid = {"base", *SETTING_TO_RATIO.keys()}
    for item in settings:
        if item not in valid:
            raise ValueError(f"Unsupported setting: {item}. Valid options: {sorted(valid)}")
        normalized.append(item)
    if not normalized:
        raise ValueError("At least one setting is required.")
    return normalized


def compute_subset_sizes(total_size: int) -> Dict[str, int]:
    sizes: Dict[str, int] = {}
    for setting, ratio in SETTING_TO_RATIO.items():
        if math.isclose(ratio, 1.0):
            size = total_size
        else:
            size = max(1, int(total_size * ratio))
        sizes[setting] = size
    return sizes


def build_nested_subsets(
    rows: List[Dict[str, Any]],
    *,
    seed: int,
    subset_dir: Path,
) -> Dict[str, Dict[str, Any]]:
    shuffled_rows = list(rows)
    rng = random.Random(seed)
    rng.shuffle(shuffled_rows)

    subset_dir.mkdir(parents=True, exist_ok=True)
    subset_sizes = compute_subset_sizes(len(shuffled_rows))
    subset_info: Dict[str, Dict[str, Any]] = {}

    for setting, size in subset_sizes.items():
        subset_rows = shuffled_rows[:size]
        subset_path = subset_dir / f"{setting}.jsonl"
        write_jsonl(subset_path, subset_rows)
        subset_info[setting] = {
            "path": subset_path,
            "train_size": len(subset_rows),
            "train_ratio": len(subset_rows) / len(shuffled_rows) if shuffled_rows else 0.0,
        }

    return subset_info


def list_child_dirs(path: Path) -> set[Path]:
    if not path.exists():
        return set()
    return {child.resolve() for child in path.iterdir() if child.is_dir()}


def discover_new_run_dir(output_root: Path, before_dirs: set[Path]) -> Optional[Path]:
    after_dirs = list_child_dirs(output_root)
    new_dirs = sorted(after_dirs - before_dirs, key=lambda item: item.stat().st_mtime, reverse=True)
    if new_dirs:
        return new_dirs[0]
    existing = sorted(after_dirs, key=lambda item: item.stat().st_mtime, reverse=True)
    return existing[0] if existing else None


def run_command(cmd: Sequence[str], log_path: Path) -> None:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("w", encoding="utf-8") as log_fh:
        log_fh.write("[command] " + " ".join(cmd) + "\n\n")
        log_fh.flush()
        completed = subprocess.run(
            list(cmd),
            stdout=log_fh,
            stderr=subprocess.STDOUT,
            text=True,
            check=False,
        )
    if completed.returncode != 0:
        raise RuntimeError(f"Command failed with exit code {completed.returncode}: {' '.join(cmd)}")


def read_summary(path: Path) -> Dict[str, Any]:
    with path.open("r", encoding="utf-8") as fh:
        return json.load(fh)


def parse_optional_float(value: Any) -> Optional[float]:
    if value in (None, "", "None"):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def extract_final_losses(model_dir: Path) -> Tuple[Optional[float], Optional[float]]:
    metrics_path = model_dir / "training_metrics.csv"
    if not metrics_path.exists():
        return None, None

    final_train_loss: Optional[float] = None
    final_eval_loss: Optional[float] = None
    with metrics_path.open("r", encoding="utf-8", newline="") as fh:
        reader = csv.DictReader(fh)
        for row in reader:
            train_loss = parse_optional_float(row.get("loss"))
            eval_loss = parse_optional_float(row.get("eval_loss"))
            if train_loss is not None:
                final_train_loss = train_loss
            if eval_loss is not None:
                final_eval_loss = eval_loss
    return final_train_loss, final_eval_loss


def format_metric(value: Optional[float]) -> str:
    if value is None:
        return ""
    return f"{value:.6f}"


def make_result_row(
    *,
    setting: str,
    train_ratio: float,
    train_size: int,
    seed: int,
    model_path: str,
    summary: Optional[Dict[str, Any]],
    final_train_loss: Optional[float],
    final_eval_loss: Optional[float],
    train_time_minutes: Optional[float],
    status: str,
    error_message: str,
) -> Dict[str, Any]:
    summary = summary or {}
    return {
        "setting": setting,
        "train_ratio": train_ratio,
        "train_size": train_size,
        "seed": seed,
        "status": status,
        "error_message": error_message,
        "model_path": model_path,
        "json_parse_pass_rate": summary.get("json_parse_pass_rate"),
        "required_keys_pass_rate": summary.get("required_keys_pass_rate"),
        "task_allocation_match_rate": summary.get("task_allocation_match_rate", summary.get("candidate_slot_match_rate")),
        "wash_task_match_rate": summary.get("wash_task_match_rate", summary.get("wash_assignment_exact_match_rate")),
        "overall_match_score": summary.get("overall_match_score", summary.get("composite_scheduler_match_score")),
        "invalid_assignment_rate": summary.get("invalid_assignment_rate"),
        "invalid_task_rate": summary.get("invalid_task_rate"),
        "invalid_agent_rate": summary.get("invalid_agent_rate"),
        "capability_violation_rate": summary.get("capability_violation_rate"),
        "final_train_loss": final_train_loss,
        "final_eval_loss": final_eval_loss,
        "train_time_minutes": train_time_minutes,
    }


def write_csv(path: Path, rows: List[Dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = [
        "setting",
        "train_ratio",
        "train_size",
        "seed",
        "status",
        "error_message",
        "model_path",
        "json_parse_pass_rate",
        "required_keys_pass_rate",
        "task_allocation_match_rate",
        "wash_task_match_rate",
        "overall_match_score",
        "invalid_assignment_rate",
        "invalid_task_rate",
        "invalid_agent_rate",
        "capability_violation_rate",
        "final_train_loss",
        "final_eval_loss",
        "train_time_minutes",
    ]
    with path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def write_json(path: Path, payload: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def plot_results(path: Path, rows: List[Dict[str, Any]]) -> None:
    try:
        import matplotlib.pyplot as plt
    except ImportError as exc:
        raise RuntimeError("matplotlib is required to generate the ablation plot.") from exc

    completed_rows = [row for row in rows if row.get("status") == "ok"]
    if not completed_rows:
        raise RuntimeError("No successful runs available for plotting.")

    completed_rows = sorted(completed_rows, key=lambda row: (float(row.get("train_ratio") or 0.0), row["setting"]))
    x_values = [int(row.get("train_size") or 0) for row in completed_rows]
    x_labels = [row["setting"] for row in completed_rows]

    plt.figure(figsize=(8, 5))
    plt.plot(
        x_values,
        [row.get("task_allocation_match_rate") for row in completed_rows],
        marker="o",
        label="Task Allocation Match Rate",
    )

    wash_values = [row.get("wash_task_match_rate") for row in completed_rows]
    if any(value is not None for value in wash_values):
        plt.plot(x_values, wash_values, marker="s", label="Wash Task Match Rate")

    overall_values = [row.get("overall_match_score") for row in completed_rows]
    if any(value is not None for value in overall_values):
        plt.plot(x_values, overall_values, marker="^", label="Overall Match Score")

    plt.title("Effect of Training Data Size on Scheduler SFT")
    plt.xlabel("Train Size")
    plt.ylabel("Match Rate")
    plt.xticks(x_values, x_labels)
    plt.ylim(0.0, 1.0)
    plt.grid(True, alpha=0.3)
    plt.legend()
    plt.tight_layout()
    path.parent.mkdir(parents=True, exist_ok=True)
    plt.savefig(path)
    plt.close()


def resolve_script_path(path: Path) -> Path:
    return path if path.is_absolute() else path.resolve()


def run_base_eval(
    *,
    python_exec: str,
    eval_script: Path,
    base_model: str,
    test_file: Path,
    output_dir: Path,
    log_path: Path,
    device: str,
    max_length: int,
    max_new_tokens: int,
    temperature: float,
    top_p: float,
) -> Dict[str, Any]:
    cmd = [
        python_exec,
        str(eval_script),
        "--model",
        base_model,
        "--base-model",
        base_model,
        "--data",
        str(test_file),
        "--output-dir",
        str(output_dir),
        "--device",
        device,
        "--max-length",
        str(max_length),
        "--max-new-tokens",
        str(max_new_tokens),
        "--temperature",
        str(temperature),
        "--top-p",
        str(top_p),
        "--save-jsonl",
    ]
    run_command(cmd, log_path)
    return read_summary(output_dir / "summary.json")


def run_train_and_eval(
    *,
    python_exec: str,
    train_script: Path,
    eval_script: Path,
    base_model: str,
    train_file: Path,
    valid_file: Path,
    test_file: Path,
    output_root: Path,
    target_name: str,
    seed: int,
    epochs: float,
    per_device_train_batch_size: int,
    per_device_eval_batch_size: int,
    gradient_accumulation_steps: int,
    learning_rate: float,
    warmup_ratio: float,
    weight_decay: float,
    max_length: int,
    lora_r: int,
    lora_alpha: int,
    lora_dropout: float,
    bf16: bool,
    fp16: bool,
    gradient_checkpointing: bool,
    device: str,
    max_new_tokens: int,
    temperature: float,
    top_p: float,
    train_log_path: Path,
    eval_log_path: Path,
    eval_output_dir: Path,
) -> Tuple[Path, Dict[str, Any], Optional[float], Optional[float], float]:
    before_dirs = list_child_dirs(output_root)
    train_cmd = [
        python_exec,
        str(train_script),
        "--train-data",
        str(train_file),
        "--eval-data",
        str(valid_file),
        "--test-data",
        str(test_file),
        "--model-name",
        base_model,
        "--output-dir",
        str(output_root),
        "--epochs",
        str(epochs),
        "--per-device-train-batch-size",
        str(per_device_train_batch_size),
        "--per-device-eval-batch-size",
        str(per_device_eval_batch_size),
        "--gradient-accumulation-steps",
        str(gradient_accumulation_steps),
        "--learning-rate",
        str(learning_rate),
        "--warmup-ratio",
        str(warmup_ratio),
        "--weight-decay",
        str(weight_decay),
        "--max-length",
        str(max_length),
        "--use-lora",
        "--lora-r",
        str(lora_r),
        "--lora-alpha",
        str(lora_alpha),
        "--lora-dropout",
        str(lora_dropout),
        "--plot-metrics",
        "--seed",
        str(seed),
        "--dataset-mode",
        "single",
        "--single-target-name",
        target_name,
    ]
    if bf16:
        train_cmd.append("--bf16")
    if fp16:
        train_cmd.append("--fp16")
    if gradient_checkpointing:
        train_cmd.append("--gradient-checkpointing")

    train_start = time.perf_counter()
    run_command(train_cmd, train_log_path)
    train_minutes = (time.perf_counter() - train_start) / 60.0

    run_dir = discover_new_run_dir(output_root, before_dirs)
    if run_dir is None:
        raise RuntimeError(f"Failed to discover a completed run directory under {output_root}")

    model_dir = run_dir / target_name
    if not model_dir.exists():
        raise RuntimeError(f"Expected trained model directory not found: {model_dir}")

    final_train_loss, final_eval_loss = extract_final_losses(model_dir)

    eval_cmd = [
        python_exec,
        str(eval_script),
        "--model",
        str(model_dir),
        "--base-model",
        base_model,
        "--data",
        str(test_file),
        "--output-dir",
        str(eval_output_dir),
        "--device",
        device,
        "--max-length",
        str(max_length),
        "--max-new-tokens",
        str(max_new_tokens),
        "--temperature",
        str(temperature),
        "--top-p",
        str(top_p),
        "--save-jsonl",
    ]
    run_command(eval_cmd, eval_log_path)
    summary = read_summary(eval_output_dir / "summary.json")
    return model_dir, summary, final_train_loss, final_eval_loss, train_minutes


def main() -> int:
    args = parse_args()
    settings = validate_settings(args.settings)

    train_file = args.train_file.resolve()
    valid_file = args.valid_file.resolve()
    test_file = args.test_file.resolve()
    output_dir = args.output_dir.resolve()
    result_dir = args.result_dir.resolve()
    train_script = resolve_script_path(args.train_script)
    eval_script = resolve_script_path(args.eval_script)

    for path in (train_file, valid_file, test_file, train_script, eval_script):
        if not path.exists():
            raise FileNotFoundError(path)

    train_rows = load_jsonl(train_file)
    subset_dir = result_dir / "sft_data_scale_subsets"
    subset_info = build_nested_subsets(train_rows, seed=args.seed, subset_dir=subset_dir)

    run_stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    aggregate_dir = result_dir / "sft_data_scale_ablation_artifacts" / run_stamp
    aggregate_dir.mkdir(parents=True, exist_ok=True)

    results_rows: List[Dict[str, Any]] = []
    detail_payload: Dict[str, Any] = {
        "generated_at": run_stamp,
        "seed": args.seed,
        "train_file": str(train_file),
        "valid_file": str(valid_file),
        "test_file": str(test_file),
        "base_model": args.base_model,
        "output_dir": str(output_dir),
        "result_dir": str(result_dir),
        "settings": settings,
        "subset_info": {
            name: {
                "path": str(info["path"]),
                "train_size": info["train_size"],
                "train_ratio": info["train_ratio"],
            }
            for name, info in subset_info.items()
        },
        "results": [],
    }

    python_exec = sys.executable
    for setting in settings:
        setting_result_dir = aggregate_dir / setting
        setting_result_dir.mkdir(parents=True, exist_ok=True)
        train_ratio = 0.0
        train_size = 0
        model_path = args.base_model if setting == "base" else ""
        summary: Optional[Dict[str, Any]] = None
        final_train_loss: Optional[float] = None
        final_eval_loss: Optional[float] = None
        train_time_minutes: Optional[float] = 0.0 if setting == "base" else None
        status = "ok"
        error_message = ""

        try:
            if setting == "base":
                summary = run_base_eval(
                    python_exec=python_exec,
                    eval_script=eval_script,
                    base_model=args.base_model,
                    test_file=test_file,
                    output_dir=setting_result_dir / "eval_test",
                    log_path=setting_result_dir / "eval.log",
                    device=args.device,
                    max_length=args.max_length,
                    max_new_tokens=args.max_new_tokens,
                    temperature=args.temperature,
                    top_p=args.top_p,
                )
            else:
                info = subset_info[setting]
                train_ratio = float(info["train_ratio"])
                train_size = int(info["train_size"])
                model_dir, summary, final_train_loss, final_eval_loss, train_time_minutes = run_train_and_eval(
                    python_exec=python_exec,
                    train_script=train_script,
                    eval_script=eval_script,
                    base_model=args.base_model,
                    train_file=Path(info["path"]),
                    valid_file=valid_file,
                    test_file=test_file,
                    output_root=output_dir / setting,
                    target_name=args.target_name,
                    seed=args.seed,
                    epochs=args.epochs,
                    per_device_train_batch_size=args.per_device_train_batch_size,
                    per_device_eval_batch_size=args.per_device_eval_batch_size,
                    gradient_accumulation_steps=args.gradient_accumulation_steps,
                    learning_rate=args.learning_rate,
                    warmup_ratio=args.warmup_ratio,
                    weight_decay=args.weight_decay,
                    max_length=args.max_length,
                    lora_r=args.lora_r,
                    lora_alpha=args.lora_alpha,
                    lora_dropout=args.lora_dropout,
                    bf16=args.bf16,
                    fp16=args.fp16,
                    gradient_checkpointing=args.gradient_checkpointing,
                    device=args.device,
                    max_new_tokens=args.max_new_tokens,
                    temperature=args.temperature,
                    top_p=args.top_p,
                    train_log_path=setting_result_dir / "train.log",
                    eval_log_path=setting_result_dir / "eval.log",
                    eval_output_dir=setting_result_dir / "eval_test",
                )
                model_path = str(model_dir)
        except Exception as exc:
            status = "failed"
            error_message = str(exc)

        row = make_result_row(
            setting=setting,
            train_ratio=train_ratio,
            train_size=train_size,
            seed=args.seed,
            model_path=model_path,
            summary=summary,
            final_train_loss=final_train_loss,
            final_eval_loss=final_eval_loss,
            train_time_minutes=train_time_minutes,
            status=status,
            error_message=error_message,
        )
        results_rows.append(row)
        detail_payload["results"].append(
            {
                **row,
                "summary": summary,
            }
        )

    csv_path = result_dir / "sft_data_scale_ablation.csv"
    json_path = result_dir / "sft_data_scale_ablation.json"
    plot_path = result_dir / "sft_data_scale_ablation.png"
    write_csv(csv_path, results_rows)
    write_json(json_path, detail_payload)

    plot_error = ""
    try:
        plot_results(plot_path, results_rows)
    except Exception as exc:
        plot_error = str(exc)
        detail_payload["plot_error"] = plot_error
        write_json(json_path, detail_payload)

    print(f"[done] Aggregate CSV: {csv_path}")
    print(f"[done] Aggregate JSON: {json_path}")
    if plot_error:
        print(f"[warn] Plot generation failed: {plot_error}")
    else:
        print(f"[done] Aggregate plot: {plot_path}")

    for row in results_rows:
        print(
            "[result] "
            f"setting={row['setting']} "
            f"status={row['status']} "
            f"train_size={row['train_size']} "
            f"task_allocation_match_rate={format_metric(parse_optional_float(row.get('task_allocation_match_rate')))} "
            f"wash_task_match_rate={format_metric(parse_optional_float(row.get('wash_task_match_rate')))} "
            f"overall_match_score={format_metric(parse_optional_float(row.get('overall_match_score')))}"
        )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
