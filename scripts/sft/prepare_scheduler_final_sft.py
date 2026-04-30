#!/usr/bin/env python3
"""
Prepare final scheduler SFT train/val/test splits from collected raw runs.
"""

from __future__ import annotations

import argparse
import csv
import itertools
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

from export_scheduler_sft_dataset import extract_samples_from_file


DEFAULT_SYSTEM_PROMPT = (
    "你是一个多智能体厨房环境中的全局调度器。"
    "请根据当前任务池、智能体状态、资源状态和触发原因，输出结构化调度决策。"
    "输出应尽量保持稳定、简洁、可执行，并与 teacher scheduler 的格式一致。"
)


@dataclass(frozen=True)
class EpisodeRecord:
    pair: str
    run_id: str
    result_path: Path
    exportable_samples: int


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Prepare final scheduler SFT splits from collected raw runs."
    )
    parser.add_argument(
        "--results-root",
        type=Path,
        default=Path("results/scheduler_sft_collection/raw_runs"),
        help="Directory containing collected scheduler run JSON files.",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("results/scheduler_sft_collection/datasets/final"),
        help="Directory to write final train/val/test JSONL files.",
    )
    parser.add_argument(
        "--train-ratio",
        type=float,
        default=0.8,
        help="Target train episode ratio.",
    )
    parser.add_argument(
        "--val-ratio",
        type=float,
        default=0.1,
        help="Target validation episode ratio.",
    )
    parser.add_argument(
        "--test-ratio",
        type=float,
        default=0.1,
        help="Target test episode ratio.",
    )
    parser.add_argument(
        "--system-prompt",
        default=DEFAULT_SYSTEM_PROMPT,
        help="Fallback system prompt used when exported scheduler samples have an empty system field.",
    )
    parser.add_argument(
        "--include-fallback",
        action="store_true",
        help="Include scheduler samples that invoked rule fallback.",
    )
    parser.add_argument(
        "--allow-incomplete",
        action="store_true",
        help="Include samples not marked exportable_for_sft.",
    )
    parser.add_argument(
        "--max-episodes",
        type=int,
        help="Optional cap on the number of episodes to include after sorting.",
    )
    parser.add_argument(
        "--exclude-substring",
        action="append",
        default=[],
        help="Exclude result paths containing this substring. Can be passed multiple times.",
    )
    return parser.parse_args()


def _pair_from_orders(orders: Sequence[str]) -> str:
    normalized = [str(item).strip() for item in orders if str(item).strip()]
    return "__".join(normalized) if normalized else "unknown_pair"


def iter_episode_records(
    results_root: Path,
    *,
    include_fallback: bool,
    allow_incomplete: bool,
    exclude_substrings: Sequence[str],
) -> Iterable[EpisodeRecord]:
    if not results_root.exists():
        return
    for result_path in sorted(results_root.rglob("experiment_*.json")):
        if not result_path.is_file():
            continue
        result_path_str = str(result_path)
        if any(substr and substr in result_path_str for substr in exclude_substrings):
            continue
        try:
            data = json.loads(result_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        orders = data.get("orders") or []
        pair = _pair_from_orders(orders)
        run_id = data.get("run_id") or result_path.parent.name
        samples = extract_samples_from_file(
            result_path,
            include_fallback=include_fallback,
            allow_incomplete=allow_incomplete,
        )
        if not samples:
            continue
        yield EpisodeRecord(
            pair=pair,
            run_id=str(run_id),
            result_path=result_path,
            exportable_samples=len(samples),
        )


def split_episode_counts(
    total: int,
    train_ratio: float,
    val_ratio: float,
    test_ratio: float,
) -> Tuple[int, int, int]:
    if total <= 0:
        raise ValueError("Episode count must be positive.")
    ratio_sum = train_ratio + val_ratio + test_ratio
    if ratio_sum <= 0:
        raise ValueError("train/val/test ratios must be positive.")

    raw = [
        total * train_ratio / ratio_sum,
        total * val_ratio / ratio_sum,
        total * test_ratio / ratio_sum,
    ]
    counts = [int(raw[0]), int(raw[1]), int(raw[2])]
    remainders = [raw[i] - counts[i] for i in range(3)]
    while sum(counts) < total:
        idx = max(range(3), key=lambda item: (remainders[item], -item))
        counts[idx] += 1
        remainders[idx] = 0.0

    if counts[0] <= 0:
        counts[0] = 1
    if total >= 3 and counts[1] <= 0:
        counts[1] = 1
        counts[0] -= 1
    if total >= 5 and counts[2] <= 0:
        counts[2] = 1
        counts[0] -= 1

    while sum(counts) > total:
        idx = max(range(3), key=lambda item: counts[item])
        if counts[idx] > 1:
            counts[idx] -= 1
        else:
            break

    return counts[0], counts[1], counts[2]


def choose_best_split(
    episodes: Sequence[EpisodeRecord],
    train_count: int,
    val_count: int,
    test_count: int,
) -> Dict[str, List[EpisodeRecord]]:
    if len(episodes) > 15:
        return choose_greedy_split(
            episodes=episodes,
            train_count=train_count,
            val_count=val_count,
            test_count=test_count,
        )

    total_samples = sum(item.exportable_samples for item in episodes)
    target_train = total_samples * train_count / len(episodes)
    target_val = total_samples * val_count / len(episodes)
    target_test = total_samples * test_count / len(episodes)

    indexed = list(episodes)
    best_score = None
    best_split = None
    all_indices = tuple(range(len(indexed)))

    for val_indices in itertools.combinations(all_indices, val_count):
        remaining_after_val = tuple(idx for idx in all_indices if idx not in val_indices)
        for test_indices in itertools.combinations(remaining_after_val, test_count):
            train_indices = tuple(
                idx for idx in remaining_after_val if idx not in test_indices
            )
            if len(train_indices) != train_count:
                continue

            train_samples = sum(indexed[idx].exportable_samples for idx in train_indices)
            val_samples = sum(indexed[idx].exportable_samples for idx in val_indices)
            test_samples = sum(indexed[idx].exportable_samples for idx in test_indices)

            score = (
                abs(train_samples - target_train),
                abs(val_samples - target_val),
                abs(test_samples - target_test),
                tuple(sorted(indexed[idx].run_id for idx in val_indices)),
                tuple(sorted(indexed[idx].run_id for idx in test_indices)),
            )
            if best_score is None or score < best_score:
                best_score = score
                best_split = {
                    "train": [indexed[idx] for idx in train_indices],
                    "val": [indexed[idx] for idx in val_indices],
                    "test": [indexed[idx] for idx in test_indices],
                }

    if best_split is None:
        raise RuntimeError("Failed to compute a valid final split.")
    return best_split


def choose_greedy_split(
    episodes: Sequence[EpisodeRecord],
    train_count: int,
    val_count: int,
    test_count: int,
) -> Dict[str, List[EpisodeRecord]]:
    total_samples = sum(item.exportable_samples for item in episodes)
    total_episodes = len(episodes)
    targets = {
        "train": total_samples * train_count / total_episodes,
        "val": total_samples * val_count / total_episodes,
        "test": total_samples * test_count / total_episodes,
    }
    limits = {"train": train_count, "val": val_count, "test": test_count}
    selected: Dict[str, List[EpisodeRecord]] = {"train": [], "val": [], "test": []}
    sample_totals = {"train": 0, "val": 0, "test": 0}

    remaining = sorted(
        episodes,
        key=lambda item: (-item.exportable_samples, item.pair, item.run_id),
    )
    for episode in remaining:
        candidates = [
            split
            for split in ("train", "val", "test")
            if len(selected[split]) < limits[split]
        ]
        if not candidates:
            break

        def split_priority(split: str) -> Tuple[float, int, str]:
            deficit = targets[split] - sample_totals[split]
            return (
                deficit,
                limits[split] - len(selected[split]),
                split,
            )

        chosen = max(candidates, key=split_priority)
        selected[chosen].append(episode)
        sample_totals[chosen] += episode.exportable_samples

    return selected


def normalize_samples(
    samples: Iterable[Dict],
    *,
    split: str,
    pair: str,
    run_id: str,
    system_prompt: str,
    round_tag: str,
) -> List[Dict]:
    normalized: List[Dict] = []
    for sample in samples:
        item = dict(sample)
        if not item.get("system"):
            item["system"] = system_prompt
        meta = dict(item.get("meta") or {})
        meta["split"] = split
        meta["pair"] = pair
        meta["round_tag"] = round_tag
        meta["training_target"] = "scheduler"
        meta["run_id"] = run_id
        item["meta"] = meta
        normalized.append(item)
    return normalized


def write_jsonl(path: Path, samples: Iterable[Dict]) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    count = 0
    with path.open("w", encoding="utf-8") as fh:
        for sample in samples:
            fh.write(json.dumps(sample, ensure_ascii=False) + "\n")
            count += 1
    return count


def build_summary_markdown(
    *,
    split_episodes: Dict[str, List[EpisodeRecord]],
    split_counts: Dict[str, int],
    output_dir: Path,
    round_tag: str,
) -> str:
    total_episodes = sum(len(items) for items in split_episodes.values())
    total_samples = sum(split_counts.values())
    lines: List[str] = []
    lines.append("# Scheduler Final SFT Split Summary")
    lines.append("")
    lines.append(f"- Round tag: {round_tag}")
    lines.append(f"- Source episodes: {total_episodes}")
    lines.append(f"- Exported samples: {total_samples}")
    lines.append(f"- Output directory: {output_dir}")
    lines.append("")
    lines.append("## Split Counts")
    lines.append("")
    lines.append("| Split | Episode Count | Sample Count |")
    lines.append("|---|---:|---:|")
    for split in ("train", "val", "test"):
        lines.append(
            f"| {split} | {len(split_episodes[split])} | {split_counts[split]} |"
        )
    lines.append("")
    lines.append("## Episode Assignment")
    lines.append("")
    lines.append("| Split | Pair | Samples | Run ID |")
    lines.append("|---|---|---:|---|")
    for split in ("train", "val", "test"):
        for item in sorted(split_episodes[split], key=lambda record: (record.pair, record.run_id)):
            lines.append(
                f"| {split} | {item.pair} | {item.exportable_samples} | {item.run_id} |"
            )
    lines.append("")
    lines.append("## Ready-To-Train Files")
    lines.append("")
    lines.append(f"- train: `{output_dir / 'train_scheduler_final.jsonl'}`")
    lines.append(f"- val: `{output_dir / 'val_scheduler_final.jsonl'}`")
    lines.append(f"- test: `{output_dir / 'test_scheduler_final.jsonl'}`")
    lines.append(f"- all: `{output_dir / 'all_scheduler_final.jsonl'}`")
    return "\n".join(lines) + "\n"


def main() -> None:
    args = parse_args()

    episodes = list(
        iter_episode_records(
            args.results_root,
            include_fallback=args.include_fallback,
            allow_incomplete=args.allow_incomplete,
            exclude_substrings=args.exclude_substring,
        )
    )
    if args.max_episodes is not None:
        episodes = episodes[: args.max_episodes]
    if not episodes:
        raise RuntimeError(f"No eligible episodes found in {args.results_root}")

    round_tag = "final_scheduler_sft_dataset"
    train_count, val_count, test_count = split_episode_counts(
        total=len(episodes),
        train_ratio=args.train_ratio,
        val_ratio=args.val_ratio,
        test_ratio=args.test_ratio,
    )
    split_episodes = choose_best_split(
        episodes=episodes,
        train_count=train_count,
        val_count=val_count,
        test_count=test_count,
    )

    split_samples: Dict[str, List[Dict]] = {"train": [], "val": [], "test": []}
    manifest_rows: List[Dict[str, object]] = []
    for split_name, items in split_episodes.items():
        for item in items:
            samples = extract_samples_from_file(
                item.result_path,
                include_fallback=args.include_fallback,
                allow_incomplete=args.allow_incomplete,
            )
            normalized = normalize_samples(
                samples,
                split=split_name,
                pair=item.pair,
                run_id=item.run_id,
                system_prompt=args.system_prompt,
                round_tag=round_tag,
            )
            split_samples[split_name].extend(normalized)
            manifest_rows.append(
                {
                    "split": split_name,
                    "pair": item.pair,
                    "run_id": item.run_id,
                    "samples": len(normalized),
                    "result_path": str(item.result_path),
                }
            )

    args.output_dir.mkdir(parents=True, exist_ok=True)
    split_counts = {
        "train": write_jsonl(args.output_dir / "train_scheduler_final.jsonl", split_samples["train"]),
        "val": write_jsonl(args.output_dir / "val_scheduler_final.jsonl", split_samples["val"]),
        "test": write_jsonl(args.output_dir / "test_scheduler_final.jsonl", split_samples["test"]),
    }
    split_counts["all"] = write_jsonl(
        args.output_dir / "all_scheduler_final.jsonl",
        split_samples["train"] + split_samples["val"] + split_samples["test"],
    )

    manifest_path = args.output_dir / "final_split_manifest.csv"
    with manifest_path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(
            fh,
            fieldnames=["split", "pair", "run_id", "samples", "result_path"],
        )
        writer.writeheader()
        for row in manifest_rows:
            writer.writerow(row)

    summary_path = args.output_dir / "final_split_summary.md"
    summary_path.write_text(
        build_summary_markdown(
            split_episodes=split_episodes,
            split_counts={key: split_counts[key] for key in ("train", "val", "test")},
            output_dir=args.output_dir,
            round_tag=round_tag,
        ),
        encoding="utf-8",
    )

    print(f"final_episodes={len(episodes)}")
    print(f"train_episodes={len(split_episodes['train'])}")
    print(f"val_episodes={len(split_episodes['val'])}")
    print(f"test_episodes={len(split_episodes['test'])}")
    print(f"train_samples={split_counts['train']}")
    print(f"val_samples={split_counts['val']}")
    print(f"test_samples={split_counts['test']}")
    print(f"all_samples={split_counts['all']}")
    print(f"manifest={manifest_path}")
    print(f"summary={summary_path}")


if __name__ == "__main__":
    main()
