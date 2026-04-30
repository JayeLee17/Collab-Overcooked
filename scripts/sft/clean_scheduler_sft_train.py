#!/usr/bin/env python3
"""
First-pass cleaning utility for scheduler SFT datasets.

Goals:
1. Keep the test split frozen.
2. Normalize response JSON structure and notes style.
3. Remove obviously invalid samples.
4. Lightly oversample multi-candidate train samples.
5. Produce cleaning reports for later thesis write-up and retraining.
"""

from __future__ import annotations

import argparse
import json
import math
from collections import Counter, defaultdict
from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple


DEFAULT_TRAIN = Path(
    "results/scheduler_sft_collection/datasets/final/train_scheduler_final.jsonl"
)
DEFAULT_VAL = Path(
    "results/scheduler_sft_collection/datasets/final/val_scheduler_final.jsonl"
)
DEFAULT_TEST = Path(
    "results/scheduler_sft_collection/datasets/final/test_scheduler_final.jsonl"
)
DEFAULT_OUTPUT_DIR = Path(
    "results/scheduler_sft_collection/datasets/final_clean_v2"
)


@dataclass
class SampleResult:
    sample: Optional[Dict[str, Any]]
    removed: Optional[Dict[str, Any]]
    rewritten: Optional[Dict[str, Any]]
    sample_type: str
    issues: List[str]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Clean scheduler SFT train/val datasets and keep test frozen."
    )
    parser.add_argument("--train-data", type=Path, default=DEFAULT_TRAIN)
    parser.add_argument("--val-data", type=Path, default=DEFAULT_VAL)
    parser.add_argument("--test-data", type=Path, default=DEFAULT_TEST)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument(
        "--oversample-multi-candidate",
        type=float,
        default=2.0,
        help="Duplicate factor for train multi-candidate samples. 1.0 disables oversampling.",
    )
    parser.add_argument(
        "--downsample-simple-duplicates",
        type=float,
        default=0.8,
        help="Keep ratio for exact duplicate simple samples.",
    )
    parser.add_argument(
        "--freeze-test",
        action="store_true",
        help="Copy the test set unchanged to output dir.",
    )
    parser.add_argument(
        "--skip-val-cleaning",
        action="store_true",
        help="Only copy val unchanged instead of cleaning it.",
    )
    parser.add_argument(
        "--keep-invalid",
        action="store_true",
        help="Keep invalid samples instead of dropping them.",
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


def dump_json_text(payload: Dict[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=False, indent=2)


def parse_response_json(response_text: str) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    text = (response_text or "").strip()
    if not text:
        return None, "empty_response"
    if text.startswith("```"):
        lines = text.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].startswith("```"):
            lines = lines[:-1]
        text = "\n".join(lines).strip()
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        return None, "json_decode_error"
    if not isinstance(payload, dict):
        return None, "response_not_object"
    return payload, None


def parse_current_state(prompt_text: str) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    marker = "CURRENT STATE:\n"
    idx = prompt_text.find(marker)
    if idx == -1:
        return None, "missing_current_state"
    json_text = prompt_text[idx + len(marker):].strip()
    try:
        payload = json.loads(json_text)
    except json.JSONDecodeError:
        return None, "current_state_json_decode_error"
    if not isinstance(payload, dict):
        return None, "current_state_not_object"
    return payload, None


def build_assignment_maps(current_state: Dict[str, Any]) -> Tuple[List[str], Dict[str, Dict[str, Any]]]:
    candidate_assignments = current_state.get("candidate_assignments") or []
    ordered_ids: List[str] = []
    assignment_by_id: Dict[str, Dict[str, Any]] = {}
    for item in candidate_assignments:
        if not isinstance(item, dict):
            continue
        cid = item.get("id")
        if not isinstance(cid, str) or not cid:
            continue
        ordered_ids.append(cid)
        assignment_by_id[cid] = item
    return ordered_ids, assignment_by_id


def normalize_selected_candidate_ids(
    value: Any,
    ordered_candidate_ids: List[str],
) -> Tuple[List[str], List[str]]:
    issues: List[str] = []
    if value is None:
        return [], ["selected_candidate_ids_missing"]
    if isinstance(value, str):
        raw_ids = [value]
        issues.append("selected_candidate_ids_string_coerced")
    elif isinstance(value, list):
        raw_ids = [item for item in value if isinstance(item, str)]
        if len(raw_ids) != len(value):
            issues.append("selected_candidate_ids_non_string_removed")
    else:
        return [], ["selected_candidate_ids_invalid_type"]

    seen = set()
    valid_ids = set(ordered_candidate_ids)
    filtered = []
    for cid in raw_ids:
        if cid not in valid_ids:
            issues.append("selected_candidate_id_not_in_candidates")
            continue
        if cid in seen:
            issues.append("selected_candidate_id_duplicate_removed")
            continue
        seen.add(cid)
        filtered.append(cid)

    order_index = {cid: idx for idx, cid in enumerate(ordered_candidate_ids)}
    filtered.sort(key=lambda cid: order_index.get(cid, 10**9))
    return filtered, issues


def normalize_wash_assignment(value: Any) -> Tuple[Optional[str], List[str]]:
    if value is None:
        return None, []
    if isinstance(value, str):
        text = value.strip()
        if not text or text.lower() == "null":
            return None, ["wash_assignment_null_string_normalized"]
        return text, []
    return None, ["wash_assignment_invalid_type"]


def classify_sample_type(
    selected_ids: List[str],
    wash_assignment: Optional[str],
    ordered_candidate_ids: List[str],
    assignment_by_id: Dict[str, Dict[str, Any]],
) -> str:
    if wash_assignment:
        return "wash_assignment"
    if not selected_ids:
        return "no_candidate"
    selected_kinds = {
        str(assignment_by_id.get(cid, {}).get("kind", "unknown"))
        for cid in selected_ids
    }
    if len(ordered_candidate_ids) > max(len(selected_ids), 0):
        return "multi_candidate_assignment"
    if selected_kinds == {"keep"}:
        return "keep_assignment"
    if selected_kinds == {"new"}:
        return "new_assignment"
    if selected_kinds == {"keep", "new"}:
        return "mixed_assignment"
    return "other_assignment"


def template_notes(
    *,
    sample_type: str,
    trigger: str,
    outgoing_count: Optional[int],
) -> str:
    trigger_lower = (trigger or "").lower()
    if sample_type == "wash_assignment":
        return "Assign wash task due to low clean dish count."
    if sample_type == "no_candidate":
        return "No eligible candidate is currently available."
    if "timeout" in trigger_lower or "blocked" in trigger_lower:
        return "Reassign task because the previous assignment is blocked."
    if sample_type == "keep_assignment" or outgoing_count == 0:
        return "Keep protected assignments to maintain stable progress."
    if sample_type == "mixed_assignment":
        return "Keep stable assignments and add new task assignments."
    if sample_type == "multi_candidate_assignment":
        return "Assign task to selected candidate assignments."
    if sample_type == "new_assignment":
        return "Assign task to available candidate assignments."
    return "Keep scheduler output stable and executable."


def normalize_response_payload(
    response_payload: Dict[str, Any],
    *,
    ordered_candidate_ids: List[str],
    assignment_by_id: Dict[str, Dict[str, Any]],
    trigger: str,
    outgoing_count: Optional[int],
) -> Tuple[Dict[str, Any], str, List[str]]:
    issues: List[str] = []
    selected_ids, selected_issues = normalize_selected_candidate_ids(
        response_payload.get("selected_candidate_ids"),
        ordered_candidate_ids,
    )
    issues.extend(selected_issues)

    wash_assignment, wash_issues = normalize_wash_assignment(
        response_payload.get("wash_assignment")
    )
    issues.extend(wash_issues)

    sample_type = classify_sample_type(
        selected_ids=selected_ids,
        wash_assignment=wash_assignment,
        ordered_candidate_ids=ordered_candidate_ids,
        assignment_by_id=assignment_by_id,
    )
    normalized_notes = template_notes(
        sample_type=sample_type,
        trigger=trigger,
        outgoing_count=outgoing_count,
    )

    payload = {
        "selected_candidate_ids": selected_ids,
        "wash_assignment": wash_assignment,
        "notes": normalized_notes,
    }
    return payload, sample_type, issues


def process_sample(row: Dict[str, Any], *, keep_invalid: bool) -> SampleResult:
    issues: List[str] = []
    response_payload, response_error = parse_response_json(row.get("response", ""))
    if response_error:
        issues.append(response_error)

    current_state, state_error = parse_current_state(row.get("prompt", ""))
    if state_error:
        issues.append(state_error)

    if response_payload is None or current_state is None:
        removed = deepcopy(row)
        removed.setdefault("meta", {})
        removed["meta"]["cleaning_removed_issues"] = issues
        if keep_invalid:
            fixed = deepcopy(row)
            fallback_payload = {
                "selected_candidate_ids": [],
                "wash_assignment": None,
                "notes": "No eligible candidate is currently available.",
            }
            fixed["response"] = dump_json_text(fallback_payload)
            fixed.setdefault("meta", {})
            fixed["meta"]["cleaning_issues"] = issues
            fixed["meta"]["cleaning_sample_type"] = "no_candidate"
            return SampleResult(
                sample=fixed,
                removed=None,
                rewritten={"before": row, "after": fixed, "issues": issues},
                sample_type="no_candidate",
                issues=issues,
            )
        return SampleResult(
            sample=None,
            removed=removed,
            rewritten=None,
            sample_type="removed_invalid",
            issues=issues,
        )

    ordered_candidate_ids, assignment_by_id = build_assignment_maps(current_state)
    if not ordered_candidate_ids:
        issues.append("candidate_assignments_missing")
        if not keep_invalid:
            removed = deepcopy(row)
            removed.setdefault("meta", {})
            removed["meta"]["cleaning_removed_issues"] = issues
            return SampleResult(
                sample=None,
                removed=removed,
                rewritten=None,
                sample_type="removed_invalid",
                issues=issues,
            )

    meta = deepcopy(row.get("meta") or {})
    normalized_payload, sample_type, normalize_issues = normalize_response_payload(
        response_payload,
        ordered_candidate_ids=ordered_candidate_ids,
        assignment_by_id=assignment_by_id,
        trigger=str(meta.get("trigger") or ""),
        outgoing_count=meta.get("outgoing_count"),
    )
    issues.extend(normalize_issues)

    normalized_row = deepcopy(row)
    normalized_row["response"] = dump_json_text(normalized_payload)
    meta["cleaning_issues"] = issues
    meta["cleaning_sample_type"] = sample_type
    normalized_row["meta"] = meta

    rewritten = None
    original_normalized = dump_json_text(response_payload)
    if original_normalized != normalized_row["response"] or issues:
        rewritten = {"before": row, "after": normalized_row, "issues": issues}

    return SampleResult(
        sample=normalized_row,
        removed=None,
        rewritten=rewritten,
        sample_type=sample_type,
        issues=issues,
    )


def exact_duplicate_key(row: Dict[str, Any]) -> str:
    return json.dumps(
        {
            "prompt": row.get("prompt", ""),
            "response": row.get("response", ""),
        },
        ensure_ascii=False,
        sort_keys=True,
    )


def downsample_simple_duplicates(
    rows: List[Dict[str, Any]],
    *,
    keep_ratio: float,
) -> Tuple[List[Dict[str, Any]], int]:
    if keep_ratio >= 1.0:
        return rows, 0
    grouped: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[exact_duplicate_key(row)].append(row)

    kept: List[Dict[str, Any]] = []
    removed = 0
    simple_types = {"keep_assignment", "new_assignment", "no_candidate"}
    for group in grouped.values():
        sample_type = str(group[0].get("meta", {}).get("cleaning_sample_type", ""))
        if len(group) == 1 or sample_type not in simple_types:
            kept.extend(group)
            continue
        keep_n = max(1, int(math.ceil(len(group) * keep_ratio)))
        kept.extend(group[:keep_n])
        removed += max(0, len(group) - keep_n)
    return kept, removed


def oversample_train_rows(
    rows: List[Dict[str, Any]],
    *,
    factor: float,
) -> Tuple[List[Dict[str, Any]], int]:
    if factor <= 1.0:
        return rows, 0
    oversampled = list(rows)
    added = 0
    for row in rows:
        sample_type = str(row.get("meta", {}).get("cleaning_sample_type", ""))
        if sample_type != "multi_candidate_assignment":
            continue
        extra_copies = max(0, int(math.floor(factor - 1.0)))
        fractional = factor - 1.0 - extra_copies
        total_extra = extra_copies + (1 if fractional > 0 else 0)
        for idx in range(total_extra):
            clone = deepcopy(row)
            clone_meta = deepcopy(clone.get("meta") or {})
            clone_meta["augmentation"] = "oversample_multi_candidate"
            clone_meta["augmentation_index"] = idx + 1
            clone["meta"] = clone_meta
            oversampled.append(clone)
            added += 1
    return oversampled, added


def summarize_types(rows: List[Dict[str, Any]]) -> Dict[str, int]:
    counter = Counter()
    for row in rows:
        sample_type = str(row.get("meta", {}).get("cleaning_sample_type", "unknown"))
        counter[sample_type] += 1
    return dict(sorted(counter.items()))


def process_split(
    rows: List[Dict[str, Any]],
    *,
    keep_invalid: bool,
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]], List[Dict[str, Any]], Counter]:
    cleaned: List[Dict[str, Any]] = []
    removed: List[Dict[str, Any]] = []
    rewritten: List[Dict[str, Any]] = []
    issue_counter: Counter = Counter()
    for row in rows:
        result = process_sample(row, keep_invalid=keep_invalid)
        for issue in result.issues:
            issue_counter[issue] += 1
        if result.sample is not None:
            cleaned.append(result.sample)
        if result.removed is not None:
            removed.append(result.removed)
        if result.rewritten is not None:
            rewritten.append(result.rewritten)
    return cleaned, removed, rewritten, issue_counter


def build_report_json(
    *,
    train_before: List[Dict[str, Any]],
    train_after_clean: List[Dict[str, Any]],
    train_after_final: List[Dict[str, Any]],
    val_before: List[Dict[str, Any]],
    val_after: List[Dict[str, Any]],
    removed_rows: List[Dict[str, Any]],
    rewritten_rows: List[Dict[str, Any]],
    train_issue_counter: Counter,
    val_issue_counter: Counter,
    duplicate_removed: int,
    oversample_added: int,
    args: argparse.Namespace,
) -> Dict[str, Any]:
    return {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "input_files": {
            "train": str(args.train_data),
            "val": str(args.val_data),
            "test": str(args.test_data),
        },
        "settings": {
            "oversample_multi_candidate": args.oversample_multi_candidate,
            "downsample_simple_duplicates": args.downsample_simple_duplicates,
            "freeze_test": args.freeze_test,
            "skip_val_cleaning": args.skip_val_cleaning,
            "keep_invalid": args.keep_invalid,
        },
        "counts": {
            "train_input": len(train_before),
            "train_after_clean_before_sampling": len(train_after_clean),
            "train_output": len(train_after_final),
            "val_input": len(val_before),
            "val_output": len(val_after),
            "removed_samples_total": len(removed_rows),
            "rewritten_samples_total": len(rewritten_rows),
            "duplicate_rows_removed_from_train": duplicate_removed,
            "oversampled_rows_added_to_train": oversample_added,
        },
        "train_sample_types_after_clean": summarize_types(train_after_clean),
        "train_sample_types_output": summarize_types(train_after_final),
        "val_sample_types_output": summarize_types(val_after),
        "issue_counts": {
            "train": dict(sorted(train_issue_counter.items())),
            "val": dict(sorted(val_issue_counter.items())),
        },
    }


def build_report_markdown(report: Dict[str, Any]) -> str:
    counts = report["counts"]
    settings = report["settings"]
    lines = [
        "# Scheduler SFT Dataset Cleaning Report",
        "",
        "## Summary",
        "",
        f"- Generated at: `{report['generated_at']}`",
        f"- Train input: `{counts['train_input']}`",
        f"- Train output: `{counts['train_output']}`",
        f"- Val input: `{counts['val_input']}`",
        f"- Val output: `{counts['val_output']}`",
        f"- Removed samples: `{counts['removed_samples_total']}`",
        f"- Rewritten samples: `{counts['rewritten_samples_total']}`",
        f"- Train duplicate rows removed: `{counts['duplicate_rows_removed_from_train']}`",
        f"- Train oversampled rows added: `{counts['oversampled_rows_added_to_train']}`",
        "",
        "## Settings",
        "",
        f"- oversample_multi_candidate: `{settings['oversample_multi_candidate']}`",
        f"- downsample_simple_duplicates: `{settings['downsample_simple_duplicates']}`",
        f"- freeze_test: `{settings['freeze_test']}`",
        f"- skip_val_cleaning: `{settings['skip_val_cleaning']}`",
        f"- keep_invalid: `{settings['keep_invalid']}`",
        "",
        "## Train Sample Types",
        "",
    ]
    for key, value in report["train_sample_types_output"].items():
        lines.append(f"- {key}: `{value}`")
    lines.extend(["", "## Train Issues", ""])
    train_issues = report["issue_counts"]["train"]
    if train_issues:
        for key, value in train_issues.items():
            lines.append(f"- {key}: `{value}`")
    else:
        lines.append("- none")
    lines.extend(["", "## Val Issues", ""])
    val_issues = report["issue_counts"]["val"]
    if val_issues:
        for key, value in val_issues.items():
            lines.append(f"- {key}: `{value}`")
    else:
        lines.append("- none")
    return "\n".join(lines) + "\n"


def copy_jsonl(src: Path, dst: Path) -> None:
    rows = load_jsonl(src)
    write_jsonl(dst, rows)


def main() -> None:
    args = parse_args()
    output_dir = args.output_dir
    output_dir.mkdir(parents=True, exist_ok=True)

    train_rows = load_jsonl(args.train_data)
    val_rows = load_jsonl(args.val_data)

    train_cleaned, train_removed, train_rewritten, train_issues = process_split(
        train_rows,
        keep_invalid=args.keep_invalid,
    )
    train_after_dedup, duplicate_removed = downsample_simple_duplicates(
        train_cleaned,
        keep_ratio=args.downsample_simple_duplicates,
    )
    train_final, oversample_added = oversample_train_rows(
        train_after_dedup,
        factor=args.oversample_multi_candidate,
    )

    if args.skip_val_cleaning:
        val_final = val_rows
        val_removed: List[Dict[str, Any]] = []
        val_rewritten: List[Dict[str, Any]] = []
        val_issues: Counter = Counter()
    else:
        val_final, val_removed, val_rewritten, val_issues = process_split(
            val_rows,
            keep_invalid=args.keep_invalid,
        )

    removed_rows = train_removed + val_removed
    rewritten_rows = train_rewritten + val_rewritten

    write_jsonl(output_dir / "train_scheduler_final_clean_v2.jsonl", train_final)
    write_jsonl(output_dir / "val_scheduler_final_clean_v2.jsonl", val_final)
    if args.freeze_test:
        copy_jsonl(
            args.test_data,
            output_dir / "test_scheduler_final_frozen_copy.jsonl",
        )

    write_jsonl(output_dir / "removed_samples.jsonl", removed_rows)
    write_jsonl(output_dir / "rewritten_samples.jsonl", rewritten_rows)

    report = build_report_json(
        train_before=train_rows,
        train_after_clean=train_after_dedup,
        train_after_final=train_final,
        val_before=val_rows,
        val_after=val_final,
        removed_rows=removed_rows,
        rewritten_rows=rewritten_rows,
        train_issue_counter=train_issues,
        val_issue_counter=val_issues,
        duplicate_removed=duplicate_removed,
        oversample_added=oversample_added,
        args=args,
    )
    (output_dir / "cleaning_report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    (output_dir / "cleaning_report.md").write_text(
        build_report_markdown(report),
        encoding="utf-8",
    )

    print(f"train_input={len(train_rows)}")
    print(f"train_output={len(train_final)}")
    print(f"val_input={len(val_rows)}")
    print(f"val_output={len(val_final)}")
    print(f"removed_samples={len(removed_rows)}")
    print(f"rewritten_samples={len(rewritten_rows)}")
    print(f"duplicate_rows_removed={duplicate_removed}")
    print(f"oversampled_rows_added={oversample_added}")
    print(f"output_dir={output_dir}")


if __name__ == "__main__":
    main()
