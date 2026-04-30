#!/usr/bin/env python3
"""
Apply P0 fixes to scheduler SFT datasets:
1. Canonicalize selected_candidate_ids formatting/order
2. Resolve local semantic-signature label conflicts by majority vote

This is intended for the current scheduler JSON format.
"""

from __future__ import annotations

import argparse
import json
from collections import Counter, defaultdict
from copy import deepcopy
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple


DEFAULT_TRAIN = Path(
    "results/scheduler_sft_collection/datasets/final_clean_v3_candidate_canonical/train_scheduler_final_candidate_canonical.jsonl"
)
DEFAULT_VAL = Path(
    "results/scheduler_sft_collection/datasets/final_clean_v3_candidate_canonical/val_scheduler_final_candidate_canonical.jsonl"
)
DEFAULT_TEST = Path(
    "results/scheduler_sft_collection/datasets/final_clean_v3_candidate_canonical/test_scheduler_final_frozen_copy.jsonl"
)
DEFAULT_OUTPUT_DIR = Path(
    "results/scheduler_sft_collection/datasets/final_clean_v3_p0_fixed"
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Fix P0 scheduler dataset issues.")
    parser.add_argument("--train-data", type=Path, default=DEFAULT_TRAIN)
    parser.add_argument("--val-data", type=Path, default=DEFAULT_VAL)
    parser.add_argument("--test-data", type=Path, default=DEFAULT_TEST)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument(
        "--min-majority-count",
        type=int,
        default=2,
        help="Minimum count required before rewriting minority labels in a conflict group.",
    )
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


def parse_response(response_text: str) -> Dict[str, Any]:
    payload = json.loads(response_text)
    if not isinstance(payload, dict):
        raise ValueError("response must be a JSON object")
    return payload


def dump_response(payload: Dict[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=False, indent=2)


def normalize_candidate_ids(value: Any) -> List[str]:
    if value is None:
        return []
    if isinstance(value, list):
        values = value
    else:
        values = [value]
    cleaned: List[str] = []
    seen = set()
    for item in values:
        if item is None:
            continue
        text = str(item).strip()
        if not text or text in seen:
            continue
        seen.add(text)
        cleaned.append(text)
    return sorted(cleaned)


def parse_current_state(prompt_text: str) -> Dict[str, Any]:
    marker = "CURRENT STATE:\n"
    idx = prompt_text.find(marker)
    if idx == -1:
        raise ValueError("CURRENT STATE block not found")
    payload = json.loads(prompt_text[idx + len(marker):].strip())
    if not isinstance(payload, dict):
        raise ValueError("CURRENT STATE must be a JSON object")
    return payload


def build_semantic_signature(row: Dict[str, Any]) -> str:
    state = parse_current_state(row.get("prompt", ""))
    tasks = []
    for task in state.get("tasks", []) or []:
        tasks.append(
            (
                task.get("task_name"),
                task.get("estimated_stage"),
                task.get("desired_resource"),
                bool(task.get("protected_task")),
                bool(task.get("near_finish")),
                bool(task.get("delivery_priority")),
                tuple(task.get("missing_roles") or []),
                task.get("remaining_steps_estimate"),
                task.get("status"),
            )
        )
    agents = []
    for agent in state.get("agents", []) or []:
        agents.append(
            (
                agent.get("agent"),
                agent.get("role"),
                bool(agent.get("busy")),
                bool(agent.get("idle")),
                agent.get("current_task_id"),
                agent.get("held_object"),
            )
        )
    resources = state.get("resources", {}) or {}
    candidate_assignments = []
    for item in state.get("candidate_assignments", []) or []:
        candidate_assignments.append(
            (
                item.get("kind"),
                item.get("agent"),
                item.get("task_id"),
                item.get("role"),
                item.get("score"),
                bool(item.get("must_keep", False)),
            )
        )

    signature = {
        "tasks": tasks,
        "agents": agents,
        "candidate_assignments": candidate_assignments,
        "resources": {
            "clean_dishes": resources.get("clean_dishes"),
            "dirty_dishes": resources.get("dirty_dishes"),
            "pot_busy": resources.get("pot_busy"),
            "oven_busy": resources.get("oven_busy"),
            "chopping_board_busy": resources.get("chopping_board_busy"),
            "blender_busy": resources.get("blender_busy"),
        },
        "trigger": (row.get("meta") or {}).get("trigger"),
        "outgoing_count": (row.get("meta") or {}).get("outgoing_count"),
    }
    return json.dumps(signature, ensure_ascii=False, sort_keys=True)


def canonicalize_row(
    row: Dict[str, Any],
) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    rewritten = deepcopy(row)
    response = parse_response(rewritten["response"])
    original_ids = response.get("selected_candidate_ids")
    normalized_ids = normalize_candidate_ids(original_ids)
    response["selected_candidate_ids"] = normalized_ids
    rewritten["response"] = dump_response(response)
    meta = deepcopy(rewritten.get("meta") or {})
    meta["p0_candidate_ids_canonicalized"] = True
    meta["p0_original_candidate_ids"] = original_ids
    rewritten["meta"] = meta
    info = {
        "changed_order_only": list(original_ids or []) != normalized_ids,
        "canonical_candidate_ids": normalized_ids,
        "wash_assignment": response.get("wash_assignment"),
    }
    return rewritten, info


def resolve_split(
    rows: List[Dict[str, Any]],
    *,
    split_name: str,
    min_majority_count: int,
) -> Tuple[List[Dict[str, Any]], Dict[str, Any], List[Dict[str, Any]]]:
    canonical_rows: List[Dict[str, Any]] = []
    canonical_infos: List[Dict[str, Any]] = []
    changed_order_count = 0

    for row in rows:
        rewritten, info = canonicalize_row(row)
        canonical_rows.append(rewritten)
        canonical_infos.append(info)
        if info["changed_order_only"]:
            changed_order_count += 1

    groups: Dict[str, List[int]] = defaultdict(list)
    for idx, row in enumerate(canonical_rows):
        groups[build_semantic_signature(row)].append(idx)

    conflict_rows: List[Dict[str, Any]] = []
    rewritten_conflict_count = 0
    unresolved_conflict_groups = 0
    conflict_group_count = 0

    for signature, indices in groups.items():
        label_counter: Counter = Counter()
        label_examples: Dict[Tuple[str, ...], int] = {}
        for idx in indices:
            response = parse_response(canonical_rows[idx]["response"])
            label = tuple(response.get("selected_candidate_ids") or [])
            label_counter[label] += 1
            label_examples.setdefault(label, idx)

        if len(label_counter) <= 1:
            continue

        conflict_group_count += 1
        majority_label, majority_count = label_counter.most_common(1)[0]
        if majority_count < min_majority_count:
            unresolved_conflict_groups += 1
            continue

        for idx in indices:
            response = parse_response(canonical_rows[idx]["response"])
            current_label = tuple(response.get("selected_candidate_ids") or [])
            if current_label == majority_label:
                continue
            response["selected_candidate_ids"] = list(majority_label)
            canonical_rows[idx]["response"] = dump_response(response)
            meta = deepcopy(canonical_rows[idx].get("meta") or {})
            meta["p0_conflict_resolved"] = True
            meta["p0_majority_candidate_ids"] = list(majority_label)
            canonical_rows[idx]["meta"] = meta
            rewritten_conflict_count += 1
            conflict_rows.append(
                {
                    "split": split_name,
                    "sample_index": idx,
                    "trigger": meta.get("trigger"),
                    "pair": meta.get("pair"),
                    "source_file": meta.get("source_file"),
                    "old_candidate_ids": list(current_label),
                    "new_candidate_ids": list(majority_label),
                    "majority_count": majority_count,
                    "group_size": len(indices),
                    "all_labels": {str(list(k)): v for k, v in label_counter.items()},
                }
            )

    summary = {
        "split": split_name,
        "input_rows": len(rows),
        "candidate_order_rows_rewritten": changed_order_count,
        "conflict_group_count": conflict_group_count,
        "conflict_rows_rewritten": rewritten_conflict_count,
        "unresolved_conflict_groups": unresolved_conflict_groups,
    }
    return canonical_rows, summary, conflict_rows


def write_report(output_dir: Path, report: Dict[str, Any]) -> None:
    (output_dir / "p0_fix_report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    lines = [
        "# Scheduler P0 Fix Report",
        "",
        f"- Generated at: `{report['generated_at']}`",
        f"- Train order rewrites: `{report['train_summary']['candidate_order_rows_rewritten']}`",
        f"- Train conflict group count: `{report['train_summary']['conflict_group_count']}`",
        f"- Train conflict rows rewritten: `{report['train_summary']['conflict_rows_rewritten']}`",
        f"- Test order rewrites: `{report['test_summary']['candidate_order_rows_rewritten']}`",
        f"- Test conflict group count: `{report['test_summary']['conflict_group_count']}`",
        f"- Test conflict rows rewritten: `{report['test_summary']['conflict_rows_rewritten']}`",
        "",
        "## Notes",
        "",
        "- `candidate_order_rows_rewritten` means canonical sorting/formatting changes.",
        "- `conflict_rows_rewritten` means the row label was replaced by the local majority label under the same semantic signature.",
        "",
    ]
    (output_dir / "p0_fix_report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    args = parse_args()
    output_dir = args.output_dir
    output_dir.mkdir(parents=True, exist_ok=True)

    train_rows = load_jsonl(args.train_data)
    val_rows = load_jsonl(args.val_data)
    test_rows = load_jsonl(args.test_data)

    train_out, train_summary, train_conflicts = resolve_split(
        train_rows,
        split_name="train",
        min_majority_count=args.min_majority_count,
    )
    val_out, val_summary, val_conflicts = resolve_split(
        val_rows,
        split_name="val",
        min_majority_count=args.min_majority_count,
    )
    test_out, test_summary, test_conflicts = resolve_split(
        test_rows,
        split_name="test",
        min_majority_count=args.min_majority_count,
    )

    write_jsonl(output_dir / "train_scheduler_final_p0_fixed.jsonl", train_out)
    write_jsonl(output_dir / "val_scheduler_final_p0_fixed.jsonl", val_out)
    write_jsonl(output_dir / "test_scheduler_final_p0_fixed.jsonl", test_out)
    write_jsonl(output_dir / "train_conflict_rewrites.jsonl", train_conflicts)
    write_jsonl(output_dir / "val_conflict_rewrites.jsonl", val_conflicts)
    write_jsonl(output_dir / "test_conflict_rewrites.jsonl", test_conflicts)

    report = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "input_files": {
            "train": str(args.train_data),
            "val": str(args.val_data),
            "test": str(args.test_data),
        },
        "settings": {
            "min_majority_count": args.min_majority_count,
        },
        "train_summary": train_summary,
        "val_summary": val_summary,
        "test_summary": test_summary,
    }
    write_report(output_dir, report)

    print(f"train_input={len(train_rows)}")
    print(f"train_output={len(train_out)}")
    print(f"val_input={len(val_rows)}")
    print(f"val_output={len(val_out)}")
    print(f"test_input={len(test_rows)}")
    print(f"test_output={len(test_out)}")
    print(f"output_dir={output_dir}")


if __name__ == "__main__":
    main()
