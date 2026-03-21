#!/usr/bin/env python3
"""
Generate experiment YAMLs (E1-E9) for scheduler comparison:
  - off  (no global scheduler)
  - rule (capability_busy)
  - llm  (LLMGlobalScheduler)

This script reads configs/default.yaml as a base template and only overrides:
  - environment.horizon / environment.max_steps
  - environment.orders
  - environment.num_concurrent_tasks / environment.max_total_tasks
  - environment.global_scheduler.enabled/mode
  - run.run_id (set per file for clean results dirs)

Output directory:
  configs/exp/

Usage:
  python scripts/gen_experiment_yamls.py
  python scripts/gen_experiment_yamls.py --base configs/default.yaml --out configs/exp
"""

from __future__ import annotations

import argparse
import copy
from pathlib import Path
from typing import Any, Dict, List, Tuple

import yaml


EXPERIMENTS: List[Dict[str, Any]] = [
    {
        "id": "E1",
        "num_tasks": 2,
        "difficulty": (1, 1),
        "orders": ["boiled_egg", "baked_sweet_potato"],
        "T": 35,
        "purpose": "low-complex baseline",
    },
    {
        "id": "E2",
        "num_tasks": 2,
        "difficulty": (1, 3),
        "orders": ["boiled_egg", "baked_carrot_soup"],
        "T": 50,
        "purpose": "simple + multi-stage parallel",
    },
    {
        "id": "E3",
        "num_tasks": 2,
        "difficulty": (2, 3),
        "orders": ["baked_potato_slices", "baked_carrot_soup"],
        "T": 60,
        "purpose": "resource conflict (oven/prep/cook)",
    },
    {
        "id": "E4",
        "num_tasks": 3,
        "difficulty": (1, 1, 1),
        "orders": ["boiled_egg", "boiled_mushroom", "baked_sweet_potato"],
        "T": 45,
        "purpose": "more tasks, low complexity",
    },
    {
        "id": "E5",
        "num_tasks": 3,
        "difficulty": (1, 2, 3),
        "orders": ["boiled_egg", "baked_potato_slices", "baked_carrot_soup"],
        "T": 70,
        "purpose": "main group (clear complexity ramp)",
    },
    {
        "id": "E6",
        "num_tasks": 3,
        "difficulty": (2, 3, 4),
        "orders": ["boiled_potato_slices", "baked_mushroom_soup", "sliced_bell_pepper_and_corn_stew"],
        "T": 90,
        "purpose": "mid-high multi-task comparison",
    },
    {
        "id": "E7",
        "num_tasks": 4,
        "difficulty": (1, 1, 2, 3),
        "orders": ["boiled_egg", "boiled_mushroom", "baked_potato_slices", "baked_carrot_soup"],
        "T": 80,
        "purpose": "high load; scheduler impact emerges",
    },
    {
        "id": "E8",
        "num_tasks": 4,
        "difficulty": (1, 2, 3, 4),
        "orders": ["boiled_egg", "baked_potato_slices", "baked_carrot_soup", "sliced_bell_pepper_and_corn_stew"],
        "T": 100,
        "purpose": "paper main group",
    },
    {
        "id": "E9",
        "num_tasks": 4,
        "difficulty": (2, 3, 4, 5),
        "orders": ["baked_potato_slices", "baked_carrot_soup", "sliced_bell_pepper_and_lentil_stew", "mashed_carrot_and_chickpea_patty"],
        "T": 120,
        "purpose": "high complexity stress test",
    },
]


SCHEDULER_MODES: List[Tuple[str, Dict[str, Any]]] = [
    ("off", {"enabled": False, "mode": "off"}),
    ("rule", {"enabled": True, "mode": "capability_busy"}),
    ("llm", {"enabled": True, "mode": "llm"}),
]


def deep_update(dst: Dict[str, Any], src: Dict[str, Any]) -> Dict[str, Any]:
    for k, v in src.items():
        if isinstance(v, dict) and isinstance(dst.get(k), dict):
            deep_update(dst[k], v)
        else:
            dst[k] = v
    return dst


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", type=str, default="configs/default.yaml")
    parser.add_argument("--out", type=str, default="configs/exp")
    args = parser.parse_args()

    base_path = Path(args.base)
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    with base_path.open("r", encoding="utf-8") as f:
        base_cfg = yaml.safe_load(f)

    for exp in EXPERIMENTS:
        exp_id = exp["id"]
        orders = exp["orders"]
        T = int(exp["T"])
        n = int(exp["num_tasks"])

        for mode_name, sched_patch in SCHEDULER_MODES:
            cfg = copy.deepcopy(base_cfg)

            env = cfg.setdefault("environment", {})
            env["horizon"] = T
            env["max_steps"] = T
            env["orders"] = list(orders)
            env["num_concurrent_tasks"] = n
            env["max_total_tasks"] = n

            gs = env.setdefault("global_scheduler", {})
            deep_update(gs, sched_patch)

            run = cfg.setdefault("run", {})
            run["run_id"] = f"{exp_id}_{mode_name}_T{T}"

            # Helpful metadata for humans (ignored by loader)
            cfg.setdefault("_experiment", {})
            cfg["_experiment"] = {
                "id": exp_id,
                "mode": mode_name,
                "T": T,
                "num_tasks": n,
                "difficulty": list(exp["difficulty"]),
                "orders": list(orders),
                "purpose": exp.get("purpose", ""),
            }

            out_path = out_dir / f"{exp_id}_{mode_name}_T{T}.yaml"
            with out_path.open("w", encoding="utf-8") as f:
                yaml.safe_dump(cfg, f, sort_keys=False, allow_unicode=True)

    print(f"Wrote {len(EXPERIMENTS) * len(SCHEDULER_MODES)} files to: {out_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

