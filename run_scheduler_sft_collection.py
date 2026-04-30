#!/usr/bin/env python3
"""
Run repeated scheduler collection jobs until the target number of SFT samples is reached.
"""

from __future__ import annotations

import argparse
import itertools
import json
import random
import re
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Sequence, Tuple

try:
    import yaml
except ImportError as exc:  # pragma: no cover
    raise SystemExit("PyYAML is required to run scheduler collection.") from exc


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.sft.export_scheduler_sft_dataset import export_scheduler_dataset


def resolve_recipe_dir() -> Path:
    candidates = [
        REPO_ROOT / "collab_overcooked/prompts/recipe",
        Path.cwd() / "collab_overcooked/prompts/recipe",
        Path.cwd() / "Collab-Overcooked/collab_overcooked/prompts/recipe",
    ]
    for candidate in candidates:
        if candidate.exists():
            return candidate
    raise FileNotFoundError(
        "Could not locate collab_overcooked/prompts/recipe from the current repo context."
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Collect scheduler SFT data with balanced task-pair coverage."
    )
    parser.add_argument(
        "--config-template",
        type=Path,
        default=REPO_ROOT / "configs/sft/scheduler_sft_collect_level1.yaml",
        help="Template YAML used to generate per-run configs.",
    )
    parser.add_argument(
        "--levels",
        type=int,
        nargs="+",
        default=[1, 2, 3],
        help="Recipe difficulty levels to include in the candidate task pool.",
    )
    parser.add_argument(
        "--tasks-per-run",
        type=int,
        default=2,
        help="Number of tasks injected into each collection run.",
    )
    parser.add_argument(
        "--selection-mode",
        choices=["balanced_pairs", "random"],
        default="balanced_pairs",
        help="How to choose task combinations for each run.",
    )
    parser.add_argument(
        "--target-samples",
        type=int,
        default=100,
        help="Stop once at least this many scheduler samples have been exported.",
    )
    parser.add_argument(
        "--max-runs",
        type=int,
        default=100,
        help="Safety cap on number of experiment runs.",
    )
    parser.add_argument(
        "--seed",
        type=int,
        default=20260414,
        help="Random seed for selecting task pairs.",
    )
    parser.add_argument(
        "--python-bin",
        default=sys.executable,
        help="Python interpreter used to launch collab_overcooked.main.",
    )
    parser.add_argument(
        "--results-root",
        type=Path,
        default=REPO_ROOT / "results/scheduler_sft_collection/raw_runs",
        help="Directory for raw scheduler collection runs.",
    )
    parser.add_argument(
        "--dataset-output",
        type=Path,
        default=REPO_ROOT / "results/scheduler_sft_collection/datasets/scheduler_sft_dataset.jsonl",
        help="Exported scheduler dataset JSONL path.",
    )
    parser.add_argument(
        "--generated-config-dir",
        type=Path,
        default=REPO_ROOT / "results/scheduler_sft_collection/generated_configs",
        help="Directory for generated per-run YAML configs.",
    )
    parser.add_argument(
        "--log-dir",
        type=Path,
        default=REPO_ROOT / "results/scheduler_sft_collection/run_logs",
        help="Directory for per-run stdout/stderr logs.",
    )
    return parser.parse_args()


def discover_tasks_by_level(recipe_dir: Path) -> Dict[int, List[str]]:
    tasks_by_level: Dict[int, List[str]] = {}
    for recipe_file in sorted(recipe_dir.glob("*.txt")):
        stem = recipe_file.stem
        match = re.match(r"^(\d+)_(.+)$", stem)
        if not match:
            continue
        level = int(match.group(1))
        task_name = match.group(2)
        tasks_by_level.setdefault(level, []).append(task_name)
    return tasks_by_level


def select_candidate_tasks(levels: Sequence[int]) -> List[str]:
    tasks_by_level = discover_tasks_by_level(resolve_recipe_dir())
    selected: List[str] = []
    for level in levels:
        selected.extend(tasks_by_level.get(int(level), []))
    unique_tasks = sorted(dict.fromkeys(selected))
    if not unique_tasks:
        raise ValueError(f"No recipe tasks found for levels={list(levels)}")
    return unique_tasks


def load_manifest_pair_counts(manifest_path: Path) -> Dict[Tuple[str, ...], int]:
    counts: Dict[Tuple[str, ...], int] = {}
    if not manifest_path.exists():
        return counts
    with manifest_path.open("r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                payload = json.loads(line)
            except json.JSONDecodeError:
                continue
            pair = payload.get("pair")
            if not isinstance(pair, list):
                continue
            key = tuple(sorted(str(item) for item in pair))
            if key:
                counts[key] = counts.get(key, 0) + 1
    return counts


def choose_task_group(
    *,
    rng: random.Random,
    candidate_tasks: Sequence[str],
    tasks_per_run: int,
    selection_mode: str,
    pair_counts: Dict[Tuple[str, ...], int],
) -> List[str]:
    if len(candidate_tasks) < tasks_per_run:
        raise ValueError(
            f"Need at least {tasks_per_run} tasks, but only found {len(candidate_tasks)}"
        )
    if selection_mode == "random":
        return sorted(rng.sample(list(candidate_tasks), tasks_per_run))

    combinations = [
        tuple(sorted(combo))
        for combo in itertools.combinations(candidate_tasks, tasks_per_run)
    ]
    if not combinations:
        raise ValueError("No task combinations available for collection.")
    min_count = min(pair_counts.get(combo, 0) for combo in combinations)
    least_used = [combo for combo in combinations if pair_counts.get(combo, 0) == min_count]
    return list(rng.choice(least_used))


def load_yaml(path: Path) -> dict:
    with path.open("r", encoding="utf-8") as fh:
        data = yaml.safe_load(fh)
    if not isinstance(data, dict):
        raise ValueError(f"Invalid YAML template: {path}")
    return data


def dump_yaml(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        yaml.safe_dump(payload, fh, allow_unicode=True, sort_keys=False)


def count_dataset_lines(dataset_output: Path) -> int:
    if not dataset_output.exists():
        return 0
    with dataset_output.open("r", encoding="utf-8") as fh:
        return sum(1 for line in fh if line.strip())


def refresh_dataset(results_root: Path, dataset_output: Path) -> int:
    export_scheduler_dataset(results_root, dataset_output)
    return count_dataset_lines(dataset_output)


def build_run_config(template: dict, pair: Sequence[str], run_name: str, results_root: Path) -> dict:
    config = json.loads(json.dumps(template))
    environment = config.setdefault("environment", {})
    environment["orders"] = list(pair)
    environment["num_concurrent_tasks"] = 2
    environment.setdefault("max_total_tasks", 0)
    run = config.setdefault("run", {})
    run["run_id"] = run_name
    run["results_root"] = str(results_root)
    return config


def run_single_collection(
    *,
    python_bin: str,
    repo_root: Path,
    config_path: Path,
    log_path: Path,
) -> None:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        python_bin,
        "-m",
        "collab_overcooked.main",
        "--config_path",
        str(config_path),
    ]
    with log_path.open("w", encoding="utf-8") as log_fh:
        subprocess.run(cmd, cwd=repo_root, stdout=log_fh, stderr=subprocess.STDOUT, check=True)


def main() -> None:
    args = parse_args()
    template = load_yaml(args.config_template)
    rng = random.Random(args.seed)
    candidate_tasks = select_candidate_tasks(args.levels)

    args.results_root.mkdir(parents=True, exist_ok=True)
    args.generated_config_dir.mkdir(parents=True, exist_ok=True)
    args.log_dir.mkdir(parents=True, exist_ok=True)
    args.dataset_output.parent.mkdir(parents=True, exist_ok=True)

    manifest_path = args.results_root.parent / "collection_manifest.jsonl"
    current_samples = refresh_dataset(args.results_root, args.dataset_output)
    print(f"[collect] existing_scheduler_samples={current_samples}")
    print(
        f"[collect] candidate_tasks={len(candidate_tasks)} "
        f"levels={list(args.levels)} tasks_per_run={args.tasks_per_run}"
    )

    run_index = 0
    while current_samples < args.target_samples and run_index < args.max_runs:
        run_index += 1
        pair_counts = load_manifest_pair_counts(manifest_path)
        pair = choose_task_group(
            rng=rng,
            candidate_tasks=candidate_tasks,
            tasks_per_run=args.tasks_per_run,
            selection_mode=args.selection_mode,
            pair_counts=pair_counts,
        )
        pair_slug = "__".join(pair)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        run_name = f"scheduler_sft_{timestamp}_r{run_index:03d}_{pair_slug}"
        config_payload = build_run_config(template, pair, run_name, args.results_root)

        config_path = args.generated_config_dir / f"{run_name}.yaml"
        log_path = args.log_dir / f"{run_name}.log"
        dump_yaml(config_path, config_payload)

        print(
            f"[collect] run {run_index}/{args.max_runs} "
            f"pair={pair} current={current_samples} target={args.target_samples}"
        )
        run_single_collection(
            python_bin=args.python_bin,
            repo_root=REPO_ROOT,
            config_path=config_path,
            log_path=log_path,
        )

        previous_samples = current_samples
        current_samples = refresh_dataset(args.results_root, args.dataset_output)
        sample_delta = current_samples - previous_samples
        manifest_record = {
            "run_index": run_index,
            "run_name": run_name,
            "pair": pair,
            "levels": list(args.levels),
            "selection_mode": args.selection_mode,
            "config_path": str(config_path),
            "log_path": str(log_path),
            "dataset_samples_added": sample_delta,
            "dataset_samples_after_run": current_samples,
        }
        with manifest_path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(manifest_record, ensure_ascii=False) + "\n")
        print(
            f"[collect] dataset_samples_after_run={current_samples} "
            f"(added={sample_delta})"
        )

    if current_samples >= args.target_samples:
        print(f"[collect] target reached: {current_samples} >= {args.target_samples}")
    else:
        print(
            f"[collect] stopped by max-runs: collected {current_samples} samples "
            f"after {run_index} runs"
        )


if __name__ == "__main__":
    main()
