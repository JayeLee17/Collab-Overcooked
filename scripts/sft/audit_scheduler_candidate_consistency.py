#!/usr/bin/env python3
"""
Audit and optionally canonicalize selected_candidate_ids for scheduler SFT data.

This script is intentionally stronger than the first-pass cleaning step:
- it audits how often teacher-selected new candidates differ from a deterministic
  candidate choice rule under symmetric slot options;
- it can rewrite train/val samples into an alternative dataset where newly
  assigned candidates are canonicalized by slot.

The original dataset is never modified in-place.
"""

from __future__ import annotations

import argparse
import json
from collections import Counter, defaultdict
from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple


DEFAULT_TRAIN = Path(
    "results/scheduler_sft_collection/datasets/final_clean_v2/train_scheduler_final_clean_v2.jsonl"
)
DEFAULT_VAL = Path(
    "results/scheduler_sft_collection/datasets/final_clean_v2/val_scheduler_final_clean_v2.jsonl"
)
DEFAULT_TEST = Path(
    "results/scheduler_sft_collection/datasets/final_clean_v2/test_scheduler_final_frozen_copy.jsonl"
)
DEFAULT_OUTPUT_DIR = Path(
    "results/scheduler_sft_collection/datasets/final_clean_v3_candidate_canonical"
)


@dataclass
class CandidateContext:
    candidate_id: str
    assignment: Dict[str, Any]
    agent: Dict[str, Any]
    task: Dict[str, Any]
    desired_distance: int


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Audit and canonicalize selected_candidate_ids for scheduler SFT data."
    )
    parser.add_argument("--train-data", type=Path, default=DEFAULT_TRAIN)
    parser.add_argument("--val-data", type=Path, default=DEFAULT_VAL)
    parser.add_argument("--test-data", type=Path, default=DEFAULT_TEST)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument(
        "--copy-test",
        action="store_true",
        help="Copy the frozen test set into the output directory unchanged.",
    )
    parser.add_argument(
        "--audit-only",
        action="store_true",
        help="Only write audit reports without rewriting datasets.",
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


def parse_current_state(prompt_text: str) -> Dict[str, Any]:
    marker = "CURRENT STATE:\n"
    idx = prompt_text.find(marker)
    if idx == -1:
        raise ValueError("CURRENT STATE block not found")
    return json.loads(prompt_text[idx + len(marker):].strip())


def parse_response(response_text: str) -> Dict[str, Any]:
    return json.loads(response_text)


def dump_response(response: Dict[str, Any]) -> str:
    return json.dumps(response, ensure_ascii=False, indent=2)


def distance_key_from_resource(desired_resource: Optional[str]) -> Optional[str]:
    if not desired_resource:
        return None
    desired_resource = str(desired_resource)
    if desired_resource.startswith("pot"):
        return "pot"
    if desired_resource.startswith("oven"):
        return "oven"
    if desired_resource.startswith("chopping_board"):
        return "board"
    if desired_resource.startswith("water"):
        return "water"
    return None


def capability_penalty(role: str, capabilities: List[str]) -> int:
    caps = {str(item) for item in capabilities}
    if role == "dishwasher":
        return 0 if "wash_task" in caps else 1
    return 0 if "cook_task" in caps else 1


def build_context_maps(
    state: Dict[str, Any]
) -> Tuple[List[str], Dict[str, Dict[str, Any]], Dict[str, Dict[str, Any]], Dict[int, Dict[str, Any]]]:
    ordered_ids: List[str] = []
    assignment_by_id: Dict[str, Dict[str, Any]] = {}
    for item in state.get("candidate_assignments", []) or []:
        if not isinstance(item, dict):
            continue
        cid = item.get("id")
        if not isinstance(cid, str) or not cid:
            continue
        ordered_ids.append(cid)
        assignment_by_id[cid] = item
    agents_by_name = {
        str(agent.get("agent")): agent
        for agent in (state.get("agents") or [])
        if isinstance(agent, dict) and agent.get("agent") is not None
    }
    tasks_by_id = {
        int(task.get("task_id")): task
        for task in (state.get("tasks") or [])
        if isinstance(task, dict) and task.get("task_id") is not None
    }
    return ordered_ids, assignment_by_id, agents_by_name, tasks_by_id


def assignment_slot_key(assignment: Dict[str, Any]) -> Tuple[Any, ...]:
    return (
        str(assignment.get("kind")),
        assignment.get("task_id"),
        str(assignment.get("role")),
        bool(assignment.get("must_keep", False)),
    )


def build_candidate_context(
    assignment: Dict[str, Any],
    agents_by_name: Dict[str, Dict[str, Any]],
    tasks_by_id: Dict[int, Dict[str, Any]],
    candidate_id: str,
) -> CandidateContext:
    agent_name = str(assignment.get("agent"))
    task_id = int(assignment.get("task_id"))
    agent = agents_by_name.get(agent_name, {})
    task = tasks_by_id.get(task_id, {})
    resource_key = distance_key_from_resource(task.get("desired_resource"))
    distances = agent.get("distances") or {}
    desired_distance = distances.get(resource_key, 10**6) if resource_key else 10**6
    return CandidateContext(
        candidate_id=candidate_id,
        assignment=assignment,
        agent=agent,
        task=task,
        desired_distance=int(desired_distance),
    )


def candidate_rank_key(ctx: CandidateContext) -> Tuple[Any, ...]:
    assignment = ctx.assignment
    agent = ctx.agent
    task = ctx.task
    role = str(assignment.get("role"))
    capabilities = list(agent.get("capabilities") or [])
    return (
        -int(assignment.get("score", 0)),
        0 if bool(agent.get("idle")) else 1,
        capability_penalty(role, capabilities),
        0 if bool(task.get("protected_task")) else 1,
        0 if bool(task.get("near_finish")) else 1,
        ctx.desired_distance,
        int(agent.get("agent_index", 10**6)),
        str(agent.get("agent", "")),
        ctx.candidate_id,
    )


def canonicalize_selected_ids_for_row(
    row: Dict[str, Any]
) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    state = parse_current_state(row["prompt"])
    response = parse_response(row["response"])
    ordered_ids, assignment_by_id, agents_by_name, tasks_by_id = build_context_maps(state)
    order_index = {cid: idx for idx, cid in enumerate(ordered_ids)}

    selected_ids = list(response.get("selected_candidate_ids") or [])
    selected_set = set(selected_ids)

    slot_to_all_candidates: Dict[Tuple[Any, ...], List[str]] = defaultdict(list)
    for cid in ordered_ids:
        assignment = assignment_by_id[cid]
        slot_to_all_candidates[assignment_slot_key(assignment)].append(cid)

    changed_slots: List[Dict[str, Any]] = []
    output_ids: List[str] = []
    seen_output = set()

    for cid in selected_ids:
        assignment = assignment_by_id.get(cid)
        if not assignment:
            continue
        slot_key = assignment_slot_key(assignment)
        slot_candidates = slot_to_all_candidates.get(slot_key, [cid])
        if str(assignment.get("kind")) != "new" or len(slot_candidates) <= 1:
            if cid not in seen_output:
                output_ids.append(cid)
                seen_output.add(cid)
            continue

        contexts = [
            build_candidate_context(
                assignment=assignment_by_id[slot_cid],
                agents_by_name=agents_by_name,
                tasks_by_id=tasks_by_id,
                candidate_id=slot_cid,
            )
            for slot_cid in slot_candidates
        ]
        contexts.sort(key=candidate_rank_key)
        canonical_id = contexts[0].candidate_id

        chosen_id = canonical_id
        if chosen_id not in seen_output:
            output_ids.append(chosen_id)
            seen_output.add(chosen_id)

        if chosen_id != cid:
            orig_ctx = build_candidate_context(
                assignment=assignment_by_id[cid],
                agents_by_name=agents_by_name,
                tasks_by_id=tasks_by_id,
                candidate_id=cid,
            )
            new_ctx = contexts[0]
            changed_slots.append(
                {
                    "slot_key": {
                        "kind": assignment.get("kind"),
                        "task_id": assignment.get("task_id"),
                        "role": assignment.get("role"),
                        "must_keep": bool(assignment.get("must_keep", False)),
                    },
                    "original_candidate_id": cid,
                    "canonical_candidate_id": chosen_id,
                    "original_agent": orig_ctx.agent.get("agent"),
                    "canonical_agent": new_ctx.agent.get("agent"),
                    "task_name": new_ctx.task.get("task_name"),
                    "desired_resource": new_ctx.task.get("desired_resource"),
                    "original_distance": orig_ctx.desired_distance,
                    "canonical_distance": new_ctx.desired_distance,
                    "candidate_options": [
                        {
                            "candidate_id": ctx.candidate_id,
                            "agent": ctx.agent.get("agent"),
                            "agent_index": ctx.agent.get("agent_index"),
                            "distance": ctx.desired_distance,
                            "score": ctx.assignment.get("score"),
                        }
                        for ctx in contexts
                    ],
                }
            )

    output_ids.sort(key=lambda cid: order_index.get(cid, 10**9))

    rewritten = deepcopy(row)
    rewritten_response = deepcopy(response)
    rewritten_response["selected_candidate_ids"] = output_ids
    rewritten["response"] = dump_response(rewritten_response)
    rewritten_meta = deepcopy(rewritten.get("meta") or {})
    rewritten_meta["candidate_canonicalized"] = bool(changed_slots)
    rewritten_meta["candidate_canonical_changed_slots"] = len(changed_slots)
    rewritten["meta"] = rewritten_meta

    audit = {
        "changed": bool(changed_slots),
        "changed_slots": changed_slots,
        "selected_count_before": len(selected_ids),
        "selected_count_after": len(output_ids),
        "original_selected_candidate_ids": selected_ids,
        "canonical_selected_candidate_ids": output_ids,
    }
    return rewritten, audit


def process_rows(
    rows: List[Dict[str, Any]],
    *,
    split_name: str,
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]], Dict[str, Any]]:
    rewritten_rows: List[Dict[str, Any]] = []
    changed_rows: List[Dict[str, Any]] = []
    summary_counter: Counter = Counter()
    replacement_counter: Counter = Counter()
    role_counter: Counter = Counter()
    task_counter: Counter = Counter()

    for row in rows:
        rewritten, audit = canonicalize_selected_ids_for_row(row)
        rewritten_rows.append(rewritten)

        has_new_slot_choice = any(
            change.get("slot_key", {}).get("kind") == "new"
            for change in audit["changed_slots"]
        ) or any(
            True
            for _ in [1]
            if any(
                str(item.get("kind")) == "new"
                for item in parse_current_state(row["prompt"]).get("candidate_assignments", [])
            )
        )
        if has_new_slot_choice:
            summary_counter["rows_with_new_candidates"] += 1

        if audit["changed"]:
            summary_counter["rows_changed"] += 1
            changed_entry = {
                "meta": row.get("meta", {}),
                "audit": audit,
                "response_before": row["response"],
                "response_after": rewritten["response"],
            }
            changed_rows.append(changed_entry)
            for change in audit["changed_slots"]:
                summary_counter["changed_slots"] += 1
                replacement_counter[
                    (
                        str(change.get("original_agent")),
                        str(change.get("canonical_agent")),
                        str(change.get("desired_resource")),
                    )
                ] += 1
                role_counter[str(change.get("slot_key", {}).get("role"))] += 1
                task_counter[str(change.get("task_name"))] += 1

    summary = {
        "split": split_name,
        "input_rows": len(rows),
        "rows_with_new_candidates": summary_counter["rows_with_new_candidates"],
        "rows_changed": summary_counter["rows_changed"],
        "changed_slots": summary_counter["changed_slots"],
        "top_agent_replacements": [
            {
                "from_agent": key[0],
                "to_agent": key[1],
                "desired_resource": key[2],
                "count": value,
            }
            for key, value in replacement_counter.most_common(15)
        ],
        "changed_role_counts": dict(role_counter),
        "changed_task_counts": dict(task_counter),
    }
    return rewritten_rows, changed_rows, summary


def write_report(output_dir: Path, report: Dict[str, Any]) -> None:
    (output_dir / "candidate_consistency_audit.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    lines = [
        "# Candidate Consistency Audit",
        "",
        f"- Generated at: `{report['generated_at']}`",
        f"- Train rows changed: `{report['train_summary']['rows_changed']}` / `{report['train_summary']['input_rows']}`",
        f"- Train changed slots: `{report['train_summary']['changed_slots']}`",
        f"- Val rows changed: `{report['val_summary']['rows_changed']}` / `{report['val_summary']['input_rows']}`",
        f"- Val changed slots: `{report['val_summary']['changed_slots']}`",
        "",
        "## Train Top Replacements",
        "",
    ]
    top_replacements = report["train_summary"]["top_agent_replacements"]
    if top_replacements:
        for item in top_replacements:
            lines.append(
                f"- {item['from_agent']} -> {item['to_agent']} on `{item['desired_resource']}`: `{item['count']}`"
            )
    else:
        lines.append("- none")
    lines.extend(["", "## Train Changed Role Counts", ""])
    role_counts = report["train_summary"]["changed_role_counts"]
    if role_counts:
        for key, value in role_counts.items():
            lines.append(f"- {key}: `{value}`")
    else:
        lines.append("- none")

    (output_dir / "candidate_consistency_audit.md").write_text(
        "\n".join(lines) + "\n",
        encoding="utf-8",
    )


def copy_jsonl(src: Path, dst: Path) -> None:
    rows = load_jsonl(src)
    write_jsonl(dst, rows)


def main() -> None:
    args = parse_args()
    output_dir = args.output_dir
    output_dir.mkdir(parents=True, exist_ok=True)

    train_rows = load_jsonl(args.train_data)
    val_rows = load_jsonl(args.val_data)

    train_rewritten, train_changed_rows, train_summary = process_rows(
        train_rows, split_name="train"
    )
    val_rewritten, val_changed_rows, val_summary = process_rows(
        val_rows, split_name="val"
    )

    report = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "input_files": {
            "train": str(args.train_data),
            "val": str(args.val_data),
            "test": str(args.test_data),
        },
        "audit_only": args.audit_only,
        "train_summary": train_summary,
        "val_summary": val_summary,
    }
    write_report(output_dir, report)
    write_jsonl(output_dir / "train_changed_candidate_rows.jsonl", train_changed_rows)
    write_jsonl(output_dir / "val_changed_candidate_rows.jsonl", val_changed_rows)

    if not args.audit_only:
        write_jsonl(
            output_dir / "train_scheduler_final_candidate_canonical.jsonl",
            train_rewritten,
        )
        write_jsonl(
            output_dir / "val_scheduler_final_candidate_canonical.jsonl",
            val_rewritten,
        )
        if args.copy_test:
            copy_jsonl(
                args.test_data,
                output_dir / "test_scheduler_final_frozen_copy.jsonl",
            )

    print(f"train_rows={len(train_rows)}")
    print(f"train_rows_changed={train_summary['rows_changed']}")
    print(f"train_changed_slots={train_summary['changed_slots']}")
    print(f"val_rows={len(val_rows)}")
    print(f"val_rows_changed={val_summary['rows_changed']}")
    print(f"val_changed_slots={val_summary['changed_slots']}")
    print(f"output_dir={output_dir}")


if __name__ == "__main__":
    main()
