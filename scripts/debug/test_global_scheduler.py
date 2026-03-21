#!/usr/bin/env python3
"""
Quick test script for global scheduler behavior.

Usage examples:
  python scripts/debug/test_global_scheduler.py
  python scripts/debug/test_global_scheduler.py --config configs/default.yaml --mode llm
  python scripts/debug/test_global_scheduler.py --mode capability_busy --horizon 40
"""

import argparse
import datetime as dt
import json
from pathlib import Path
from typing import Any, Dict, Optional

from collab_overcooked.main import (
    convert_yaml_to_variant,
    load_config_from_yaml,
    main as run_main,
)


def _safe_int(value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _latest_result_json(run_dir: Path) -> Optional[Path]:
    files = sorted(run_dir.glob("experiment_*.json"), key=lambda p: p.stat().st_mtime)
    return files[-1] if files else None


def _print_scheduler_summary(result: Dict[str, Any]) -> None:
    scheduler_info = result.get("global_scheduler", {}) or {}
    ref = result.get("scheduler_reference", []) or []
    logs = scheduler_info.get("logs", []) or []

    print("\n========== Global Scheduler Summary ==========")
    print(f"enabled: {scheduler_info.get('enabled')}")
    print(f"timeout_steps: {scheduler_info.get('timeout_steps')}")
    print(f"mode model (if llm): {scheduler_info.get('model', '<none>')}")
    print(f"clean_dish_threshold: {scheduler_info.get('clean_dish_threshold')}")
    print(f"step log count: {len(logs)}")
    print(f"scheduler_reference count: {len(ref)}")

    if logs:
        last = logs[-1]
        print(f"last trigger: {last.get('trigger')}")
        print(f"last llm_used: {last.get('llm_used')}")
        print(f"last outgoing_count: {last.get('outgoing_count')}")
        events = last.get("events", []) or []
        if events:
            print("last events:")
            for ev in events[:8]:
                print(f"  - {ev}")
    print("==============================================\n")


def main() -> None:
    parser = argparse.ArgumentParser(description="Test global scheduler with YAML config")
    parser.add_argument("--config", type=str, default="configs/default.yaml")
    parser.add_argument(
        "--mode",
        type=str,
        default="llm",
        choices=["off", "capability_busy", "llm"],
        help="Global scheduler mode override",
    )
    parser.add_argument("--horizon", type=int, default=60, help="Episode horizon override")
    parser.add_argument("--episode", type=int, default=1, help="Episode count override")
    parser.add_argument(
        "--results-root",
        type=str,
        default="results/scheduler_test",
        help="Output root directory for this test run",
    )
    parser.add_argument(
        "--no-verbose-scheduler",
        action="store_true",
        help="Disable scheduler verbose logs in config",
    )
    args = parser.parse_args()

    config_path = Path(args.config)
    if not config_path.exists():
        raise FileNotFoundError(f"Config not found: {config_path}")

    cfg = load_config_from_yaml(str(config_path))
    env_cfg = cfg.setdefault("environment", {})
    run_cfg = cfg.setdefault("run", {})
    scheduler_cfg = env_cfg.setdefault("global_scheduler", {})

    # Override run params for a focused scheduler smoke test.
    run_id = f"scheduler-test-{dt.datetime.now().strftime('%Y%m%d-%H%M%S')}"
    run_cfg["run_id"] = run_id
    run_cfg["results_root"] = args.results_root
    run_cfg["episode"] = _safe_int(args.episode, 1)
    env_cfg["horizon"] = _safe_int(args.horizon, env_cfg.get("horizon", 60))

    # Force scheduler mode for A/B testing.
    scheduler_cfg["enabled"] = args.mode != "off"
    scheduler_cfg["mode"] = args.mode
    if args.no_verbose_scheduler:
        scheduler_cfg["verbose"] = False

    if args.mode == "llm":
        scheduler_cfg.setdefault("model", "qwen-plus")
        scheduler_cfg.setdefault("temperature", 0.0)
        scheduler_cfg.setdefault("fallback_to_rule", True)

    # Convert and run through existing main pipeline (low intrusion).
    variant = convert_yaml_to_variant(cfg)
    variant["yaml_config"] = cfg
    print(f"[SchedulerTest] run_id={run_id}")
    print(
        f"[SchedulerTest] mode={args.mode} horizon={variant.get('horizon')} "
        f"episode={variant.get('episode')} results_root={run_cfg['results_root']}"
    )
    run_main(variant=variant)

    # Resolve result path and print concise post-run report.
    orders = variant.get("orders", [variant.get("order", "task")])
    order_name = "_".join(orders) if len(orders) <= 3 else f"{orders[0]}_x{len(orders)}"
    run_dir = Path(run_cfg["results_root"]) / f"{run_id}_{order_name}"
    result_file = _latest_result_json(run_dir)
    if not result_file:
        print(f"[SchedulerTest] No result json found under: {run_dir}")
        return

    with result_file.open("r", encoding="utf-8") as fh:
        result = json.load(fh)

    print(f"[SchedulerTest] result_file={result_file}")
    _print_scheduler_summary(result)


if __name__ == "__main__":
    main()
