#!/usr/bin/env python3
"""Wait for a scheduler SFT run to finish, then evaluate train/val/test."""

from __future__ import annotations

import argparse
import os
import signal
import subprocess
import sys
import time
from pathlib import Path
from typing import Iterable, Optional


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Wait for a training PID or a finished run directory, then run "
            "scheduler eval on train/val/test splits."
        )
    )
    parser.add_argument(
        "--output-root",
        required=True,
        help="Root output directory that contains timestamped training run dirs.",
    )
    parser.add_argument(
        "--bundle-dir",
        required=True,
        help="Bundle/project directory that contains scripts/sft and results/...",
    )
    parser.add_argument(
        "--base-model",
        required=True,
        help="Base model path used by eval script.",
    )
    parser.add_argument(
        "--train-data",
        required=True,
        help="Train dataset path, relative to bundle-dir or absolute.",
    )
    parser.add_argument(
        "--val-data",
        required=True,
        help="Validation dataset path, relative to bundle-dir or absolute.",
    )
    parser.add_argument(
        "--test-data",
        required=True,
        help="Test dataset path, relative to bundle-dir or absolute.",
    )
    parser.add_argument(
        "--eval-script",
        default="scripts/sft/eval_scheduler_round1_sft.py",
        help="Eval script path, relative to bundle-dir or absolute.",
    )
    parser.add_argument(
        "--wait-pid",
        type=int,
        default=None,
        help="Optional training PID to wait for before running eval.",
    )
    parser.add_argument(
        "--poll-seconds",
        type=int,
        default=30,
        help="Polling interval while waiting.",
    )
    parser.add_argument(
        "--stable-seconds",
        type=int,
        default=10,
        help="Extra wait after PID exit or run discovery, to avoid races.",
    )
    parser.add_argument(
        "--run-dir",
        default=None,
        help="Optional explicit run dir. If omitted, auto-pick latest completed run.",
    )
    parser.add_argument(
        "--skip-train",
        action="store_true",
        help="Skip train split evaluation.",
    )
    parser.add_argument(
        "--skip-val",
        action="store_true",
        help="Skip val split evaluation.",
    )
    parser.add_argument(
        "--skip-test",
        action="store_true",
        help="Skip test split evaluation.",
    )
    parser.add_argument(
        "--also-base-test",
        action="store_true",
        help="Also evaluate the base model on the test split.",
    )
    return parser.parse_args()


def resolve_path(base_dir: Path, value: str) -> Path:
    path = Path(value)
    if path.is_absolute():
        return path
    return (base_dir / path).resolve()


def pid_exists(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def wait_for_pid(pid: int, poll_seconds: int) -> None:
    print(f"[wait] Waiting for PID {pid} to finish...", flush=True)
    while pid_exists(pid):
        print(f"[wait] PID {pid} still running. Sleep {poll_seconds}s.", flush=True)
        time.sleep(poll_seconds)
    print(f"[wait] PID {pid} has exited.", flush=True)


def completed_run_dirs(output_root: Path) -> Iterable[Path]:
    if not output_root.exists():
        return []
    run_dirs = []
    for child in output_root.iterdir():
        if not child.is_dir():
            continue
        adapter = child / "Scheduler" / "adapter_model.safetensors"
        if adapter.exists():
            run_dirs.append(child)
    return sorted(run_dirs, key=lambda p: p.stat().st_mtime, reverse=True)


def wait_for_completed_run(output_root: Path, poll_seconds: int) -> Path:
    print(f"[wait] Looking for completed runs under {output_root}", flush=True)
    while True:
        runs = list(completed_run_dirs(output_root))
        if runs:
            print(f"[wait] Found completed run: {runs[0]}", flush=True)
            return runs[0]
        print(f"[wait] No completed run yet. Sleep {poll_seconds}s.", flush=True)
        time.sleep(poll_seconds)


def run_eval(
    python_exec: str,
    eval_script: Path,
    model_path: Path,
    base_model: Path,
    data_path: Path,
    output_dir: Path,
) -> None:
    cmd = [
        python_exec,
        str(eval_script),
        "--model",
        str(model_path),
        "--base-model",
        str(base_model),
        "--data",
        str(data_path),
        "--output-dir",
        str(output_dir),
        "--save-jsonl",
    ]
    print("[eval] Running:", " ".join(cmd), flush=True)
    subprocess.run(cmd, check=True)


def main() -> int:
    args = parse_args()
    bundle_dir = Path(args.bundle_dir).resolve()
    output_root = Path(args.output_root).resolve()
    eval_script = resolve_path(bundle_dir, args.eval_script)
    base_model = Path(args.base_model).resolve()
    train_data = resolve_path(bundle_dir, args.train_data)
    val_data = resolve_path(bundle_dir, args.val_data)
    test_data = resolve_path(bundle_dir, args.test_data)

    if args.wait_pid is not None:
        wait_for_pid(args.wait_pid, args.poll_seconds)
        time.sleep(args.stable_seconds)

    if args.run_dir:
        run_dir = Path(args.run_dir).resolve()
    else:
        run_dir = wait_for_completed_run(output_root, args.poll_seconds)
        time.sleep(args.stable_seconds)

    scheduler_dir = run_dir / "Scheduler"
    adapter_path = scheduler_dir / "adapter_model.safetensors"
    if not adapter_path.exists():
        raise FileNotFoundError(f"Adapter not found: {adapter_path}")

    eval_logs = run_dir / "eval_logs"
    eval_logs.mkdir(parents=True, exist_ok=True)

    print(f"[info] Using run dir: {run_dir}", flush=True)
    print(f"[info] Eval script: {eval_script}", flush=True)

    python_exec = sys.executable

    if not args.skip_train:
        run_eval(
            python_exec,
            eval_script,
            scheduler_dir,
            base_model,
            train_data,
            run_dir / "eval_train",
        )

    if not args.skip_val:
        run_eval(
            python_exec,
            eval_script,
            scheduler_dir,
            base_model,
            val_data,
            run_dir / "eval_val",
        )

    if not args.skip_test:
        run_eval(
            python_exec,
            eval_script,
            scheduler_dir,
            base_model,
            test_data,
            run_dir / "eval_test",
        )

    if args.also_base_test and not args.skip_test:
        run_eval(
            python_exec,
            eval_script,
            base_model,
            base_model,
            test_data,
            run_dir / "eval_test_base_model",
        )

    print("[done] Auto-eval finished.", flush=True)
    print(f"[done] Run dir: {run_dir}", flush=True)
    print(f"[done] Train summary: {run_dir / 'eval_train' / 'summary.json'}", flush=True)
    print(f"[done] Val summary:   {run_dir / 'eval_val' / 'summary.json'}", flush=True)
    print(f"[done] Test summary:  {run_dir / 'eval_test' / 'summary.json'}", flush=True)
    if args.also_base_test and not args.skip_test:
        print(
            f"[done] Base test summary: {run_dir / 'eval_test_base_model' / 'summary.json'}",
            flush=True,
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
