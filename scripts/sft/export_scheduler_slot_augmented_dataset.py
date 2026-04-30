#!/usr/bin/env python3
"""
Export slot-augmented scheduler SFT datasets from an existing cleaned dataset.

This script derives `selected_slots` from:
- prompt CURRENT STATE -> candidate_assignments
- response.selected_candidate_ids

It keeps the current candidate-level supervision, but adds a more stable
slot-level target:
{
  "selected_slots": [
    {"kind": "new", "task_id": 0, "role": "chef"},
    ...
  ],
  "selected_candidate_ids": [...],
  "wash_assignment": ...,
  "notes": "..."
}
"""

from __future__ import annotations

import argparse
import json
from collections import Counter
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
    "results/scheduler_sft_collection/datasets/final_clean_v4_slot_augmented"
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Export slot-augmented scheduler SFT datasets."
    )
    parser.add_argument("--train-data", type=Path, default=DEFAULT_TRAIN)
    parser.add_argument("--val-data", type=Path, default=DEFAULT_VAL)
    parser.add_argument("--test-data", type=Path, default=DEFAULT_TEST)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
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


def parse_current_state(prompt_text: str) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    marker = "CURRENT STATE:\n"
    idx = prompt_text.find(marker)
    if idx == -1:
        return None, "missing_current_state"
    json_text = prompt_text[idx + len(marker) :].strip()
    try:
        payload = json.loads(json_text)
    except json.JSONDecodeError:
        return None, "current_state_json_decode_error"
    if not isinstance(payload, dict):
        return None, "current_state_not_object"
    return payload, None


def parse_response(response_text: str) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    text = (response_text or "").strip()
    if not text:
        return None, "empty_response"
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        return None, "response_json_decode_error"
    if not isinstance(payload, dict):
        return None, "response_not_object"
    return payload, None


def build_assignment_map(current_state: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    mapping: Dict[str, Dict[str, Any]] = {}
    for item in current_state.get("candidate_assignments") or []:
        if not isinstance(item, dict):
            continue
        cid = item.get("id")
        if isinstance(cid, str) and cid:
            mapping[cid] = item
    return mapping


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
    return cleaned


def selected_slots_from_candidate_ids(
    selected_candidate_ids: List[str],
    assignment_map: Dict[str, Dict[str, Any]],
) -> Tuple[List[Dict[str, Any]], List[str]]:
    slots: List[Dict[str, Any]] = []
    issues: List[str] = []
    seen = set()
    for cid in selected_candidate_ids:
        assignment = assignment_map.get(cid)
        if not assignment:
            issues.append("selected_candidate_id_missing_from_assignment_map")
            continue
        slot = {
            "kind": str(assignment.get("kind")),
            "task_id": assignment.get("task_id"),
            "role": str(assignment.get("role")),
        }
        slot_key = (slot["kind"], slot["task_id"], slot["role"])
        if slot_key in seen:
            continue
        seen.add(slot_key)
        slots.append(slot)
    slots.sort(key=lambda item: (str(item["kind"]), int(item["task_id"]), str(item["role"])))
    return slots, issues


def dump_response(payload: Dict[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=False, indent=2)


def process_rows(
    rows: List[Dict[str, Any]],
    *,
    split_name: str,
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]], Dict[str, Any]]:
    rewritten_rows: List[Dict[str, Any]] = []
    changed_rows: List[Dict[str, Any]] = []
    issue_counter: Counter = Counter()
    slot_count_counter: Counter = Counter()

    for row in rows:
        current_state, current_state_error = parse_current_state(row.get("prompt", ""))
        response_obj, response_error = parse_response(row.get("response", ""))
        if current_state_error:
            issue_counter[current_state_error] += 1
        if response_error:
            issue_counter[response_error] += 1

        rewritten = deepcopy(row)
        meta = deepcopy(rewritten.get("meta") or {})
        if current_state is None or response_obj is None:
            meta.setdefault("slot_augmentation_issues", [])
            if current_state_error:
                meta["slot_augmentation_issues"].append(current_state_error)
            if response_error:
                meta["slot_augmentation_issues"].append(response_error)
            rewritten["meta"] = meta
            rewritten_rows.append(rewritten)
            continue

        assignment_map = build_assignment_map(current_state)
        selected_candidate_ids = normalize_candidate_ids(
            response_obj.get("selected_candidate_ids")
        )
        selected_slots, slot_issues = selected_slots_from_candidate_ids(
            selected_candidate_ids,
            assignment_map,
        )
        for issue in slot_issues:
            issue_counter[issue] += 1
        slot_count_counter[len(selected_slots)] += 1

        augmented_response = {
            "selected_slots": selected_slots,
            "selected_candidate_ids": response_obj.get("selected_candidate_ids"),
            "wash_assignment": response_obj.get("wash_assignment"),
            "notes": response_obj.get("notes"),
        }
        rewritten["response"] = dump_response(augmented_response)
        meta["slot_augmented"] = True
        meta["selected_slot_count"] = len(selected_slots)
        if slot_issues:
            meta["slot_augmentation_issues"] = slot_issues
        rewritten["meta"] = meta
        rewritten_rows.append(rewritten)

        changed_rows.append(
            {
                "meta": row.get("meta", {}),
                "selected_candidate_ids": selected_candidate_ids,
                "selected_slots": selected_slots,
                "slot_augmentation_issues": slot_issues,
            }
        )

    summary = {
        "split": split_name,
        "input_rows": len(rows),
        "output_rows": len(rewritten_rows),
        "rows_with_selected_slots": len(changed_rows),
        "slot_count_distribution": dict(sorted(slot_count_counter.items())),
        "issue_counts": dict(sorted(issue_counter.items())),
    }
    return rewritten_rows, changed_rows, summary


def write_report(output_dir: Path, report: Dict[str, Any]) -> None:
    (output_dir / "slot_augmentation_report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    lines = [
        "# Scheduler Slot Augmentation Report",
        "",
        f"- Generated at: `{report['generated_at']}`",
        f"- Train rows: `{report['train_summary']['input_rows']}` -> `{report['train_summary']['output_rows']}`",
        f"- Val rows: `{report['val_summary']['input_rows']}` -> `{report['val_summary']['output_rows']}`",
        f"- Test rows: `{report['test_summary']['input_rows']}` -> `{report['test_summary']['output_rows']}`",
        "",
        "## Train Slot Count Distribution",
        "",
    ]
    train_slot_distribution = report["train_summary"]["slot_count_distribution"]
    if train_slot_distribution:
        for key, value in train_slot_distribution.items():
            lines.append(f"- {key} slots: `{value}`")
    else:
        lines.append("- none")
    lines.extend(["", "## Train Issues", ""])
    train_issues = report["train_summary"]["issue_counts"]
    if train_issues:
        for key, value in train_issues.items():
            lines.append(f"- {key}: `{value}`")
    else:
        lines.append("- none")
    (output_dir / "slot_augmentation_report.md").write_text(
        "\n".join(lines) + "\n",
        encoding="utf-8",
    )


def main() -> None:
    args = parse_args()
    output_dir = args.output_dir
    output_dir.mkdir(parents=True, exist_ok=True)

    train_rows = load_jsonl(args.train_data)
    val_rows = load_jsonl(args.val_data)
    test_rows = load_jsonl(args.test_data)

    train_output, train_changed_rows, train_summary = process_rows(
        train_rows, split_name="train"
    )
    val_output, val_changed_rows, val_summary = process_rows(
        val_rows, split_name="val"
    )
    test_output, test_changed_rows, test_summary = process_rows(
        test_rows, split_name="test"
    )

    write_jsonl(
        output_dir / "train_scheduler_final_slot_augmented.jsonl",
        train_output,
    )
    write_jsonl(
        output_dir / "val_scheduler_final_slot_augmented.jsonl",
        val_output,
    )
    write_jsonl(
        output_dir / "test_scheduler_final_slot_augmented.jsonl",
        test_output,
    )
    write_jsonl(output_dir / "train_slot_rows.jsonl", train_changed_rows)
    write_jsonl(output_dir / "val_slot_rows.jsonl", val_changed_rows)
    write_jsonl(output_dir / "test_slot_rows.jsonl", test_changed_rows)

    report = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "input_files": {
            "train": str(args.train_data),
            "val": str(args.val_data),
            "test": str(args.test_data),
        },
        "train_summary": train_summary,
        "val_summary": val_summary,
        "test_summary": test_summary,
    }
    write_report(output_dir, report)

    print(f"train_input={len(train_rows)}")
    print(f"train_output={len(train_output)}")
    print(f"val_input={len(val_rows)}")
    print(f"val_output={len(val_output)}")
    print(f"test_input={len(test_rows)}")
    print(f"test_output={len(test_output)}")
    print(f"output_dir={output_dir}")


if __name__ == "__main__":
    main()
