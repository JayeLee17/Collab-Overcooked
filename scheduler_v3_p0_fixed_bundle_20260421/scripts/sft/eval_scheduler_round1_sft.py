#!/usr/bin/env python3
"""
Evaluate the round1 scheduler SFT checkpoint on exported scheduler JSONL data.

This script is tailored for the current scheduler format:
{
  "selected_candidate_ids": [...],
  "wash_assignment": ...,
  "notes": "..."
}

Outputs
-------
1. Aggregate summary JSON
2. Per-sample CSV report
3. Optional detailed JSONL dump with generated text / parsed JSON

Typical usage
-------------
python scripts/sft/eval_scheduler_round1_sft.py \
  --model /path/to/Scheduler \
  --base-model /path/to/Qwen3.5-0.8B \
  --data results/scheduler_sft_collection/datasets/round1/dev_scheduler_round1.jsonl \
  --output-dir runs/eval_scheduler_round1_dev
"""

from __future__ import annotations

import argparse
import csv
import json
import math
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

import torch
from transformers import AutoModelForCausalLM, AutoTokenizer


SYMMETRIC_AGENT_GROUPS = {
    "A0": "chef_pair_0",
    "A4": "chef_pair_0",
    "A1": "assistant_pair_0",
    "A3": "assistant_pair_0",
    "A2": "dishwasher_singleton",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Evaluate scheduler round1 SFT checkpoints.")
    parser.add_argument("--model", required=True, type=Path, help="Merged model dir or LoRA adapter dir.")
    parser.add_argument("--base-model", type=Path, help="Base model path when --model is a LoRA adapter dir.")
    parser.add_argument("--data", required=True, type=Path, help="Scheduler JSONL dataset path.")
    parser.add_argument("--output-dir", required=True, type=Path, help="Directory for summary/report outputs.")
    parser.add_argument("--max-samples", type=int, default=0, help="Limit number of samples. 0 means all.")
    parser.add_argument("--max-length", type=int, default=2048, help="Max total token length for loss computation.")
    parser.add_argument("--max-new-tokens", type=int, default=256, help="Max generated tokens.")
    parser.add_argument("--temperature", type=float, default=0.0, help="Generation temperature.")
    parser.add_argument("--top-p", type=float, default=0.9, help="Sampling top-p when temperature > 0.")
    parser.add_argument("--device", choices=["cuda", "cpu"], default="cuda", help="Inference device preference.")
    parser.add_argument("--save-jsonl", action="store_true", help="Write detailed JSONL results.")
    return parser.parse_args()


def load_jsonl(path: Path, max_samples: int) -> List[Dict[str, Any]]:
    records: List[Dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            records.append(json.loads(line))
            if max_samples and len(records) >= max_samples:
                break
    return records


def normalize_candidate_ids(value: Any) -> List[str]:
    if value is None:
        return []
    if isinstance(value, list):
        items = value
    else:
        items = [value]
    cleaned = []
    for item in items:
        if item is None:
            continue
        text = str(item).strip()
        if text:
            cleaned.append(text)
    return sorted(cleaned)


def normalize_wash_assignment(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, dict):
        return json.dumps(value, ensure_ascii=False, sort_keys=True)
    if isinstance(value, list):
        return json.dumps(value, ensure_ascii=False)
    return str(value).strip()


def parse_current_state(prompt_text: str) -> Optional[Dict[str, Any]]:
    marker = "CURRENT STATE:\n"
    idx = prompt_text.find(marker)
    if idx == -1:
        return None
    json_text = prompt_text[idx + len(marker) :].strip()
    try:
        payload = json.loads(json_text)
    except Exception:
        return None
    return payload if isinstance(payload, dict) else None


def build_assignment_map(record: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    current_state = parse_current_state(record.get("prompt") or "")
    if not current_state:
        return {}
    assignment_map: Dict[str, Dict[str, Any]] = {}
    for item in current_state.get("candidate_assignments") or []:
        if not isinstance(item, dict):
            continue
        cid = item.get("id")
        if isinstance(cid, str) and cid:
            assignment_map[cid] = item
    return assignment_map


def build_task_map(record: Dict[str, Any]) -> Dict[Any, Dict[str, Any]]:
    current_state = parse_current_state(record.get("prompt") or "")
    if not current_state:
        return {}
    task_map: Dict[Any, Dict[str, Any]] = {}
    for item in current_state.get("tasks") or []:
        if not isinstance(item, dict):
            continue
        task_id = item.get("task_id")
        if task_id is not None:
            task_map[task_id] = item
    return task_map


def build_agent_map(record: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    current_state = parse_current_state(record.get("prompt") or "")
    if not current_state:
        return {}
    agent_map: Dict[str, Dict[str, Any]] = {}
    for item in current_state.get("agents") or []:
        if not isinstance(item, dict):
            continue
        agent_name = item.get("agent")
        if isinstance(agent_name, str) and agent_name:
            agent_map[agent_name] = item
    return agent_map


def agent_group(agent_name: str) -> str:
    return SYMMETRIC_AGENT_GROUPS.get(agent_name, agent_name)


def candidate_slot_signature(candidate_id: str, assignment_map: Dict[str, Dict[str, Any]]) -> Tuple[Any, ...]:
    assignment = assignment_map.get(candidate_id)
    if not assignment:
        return ("invalid_candidate", candidate_id)
    return (
        str(assignment.get("kind")),
        assignment.get("task_id"),
        str(assignment.get("role")),
    )


def candidate_relaxed_signature(candidate_id: str, assignment_map: Dict[str, Dict[str, Any]]) -> Tuple[Any, ...]:
    assignment = assignment_map.get(candidate_id)
    if not assignment:
        return ("invalid_candidate", candidate_id)
    return (
        str(assignment.get("kind")),
        assignment.get("task_id"),
        str(assignment.get("role")),
        agent_group(str(assignment.get("agent"))),
    )


def normalize_signatures(values: Iterable[Tuple[Any, ...]]) -> List[Tuple[Any, ...]]:
    return sorted(list(values), key=lambda item: json.dumps(item, ensure_ascii=False, sort_keys=False))


def try_parse_json_object(text: str) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    text = text.strip()
    if not text:
        return None, "empty_output"

    # First try the whole output directly.
    try:
        parsed = json.loads(text)
        if isinstance(parsed, dict):
            return parsed, None
    except Exception:
        pass

    # Fallback: extract the outermost {...} block.
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end <= start:
        return None, "no_json_object_found"
    snippet = text[start : end + 1]
    try:
        parsed = json.loads(snippet)
        if isinstance(parsed, dict):
            return parsed, None
        return None, "json_root_not_object"
    except Exception as exc:
        return None, f"json_parse_error: {exc}"


def _normalize_int_like(value: Any) -> Any:
    if isinstance(value, str):
        text = value.strip()
        if text.isdigit():
            return int(text)
    return value


def infer_required_capability(assignment: Dict[str, Any], task: Optional[Dict[str, Any]]) -> str:
    role = str(assignment.get("role") or "").strip().lower()
    if role == "dishwasher":
        return "wash_task"
    if task is not None:
        task_name = str(task.get("task_name") or "").strip().lower()
        if "wash" in task_name:
            return "wash_task"
    return "cook_task"


def inspect_wash_assignment(
    value: Any,
    *,
    agent_map: Dict[str, Dict[str, Any]],
    task_map: Dict[Any, Dict[str, Any]],
) -> Tuple[bool, bool, bool]:
    if value is None:
        return False, False, False

    invalid_task = False
    invalid_agent = False
    capability_violation = False

    items: List[Any]
    if isinstance(value, list):
        items = value
    else:
        items = [value]

    for item in items:
        if not isinstance(item, dict):
            continue

        agent_name = None
        for key in ("agent", "agent_id", "assignee", "washer"):
            raw_value = item.get(key)
            if isinstance(raw_value, str) and raw_value.strip():
                agent_name = raw_value.strip()
                break

        task_id = None
        for key in ("task_id", "task", "target_task_id", "assignment_task_id"):
            if key in item:
                task_id = _normalize_int_like(item.get(key))
                break

        if agent_name is not None and agent_name not in agent_map:
            invalid_agent = True
        if task_id is not None and task_id not in task_map:
            invalid_task = True

        if agent_name is not None and agent_name in agent_map:
            capabilities = set(agent_map[agent_name].get("capabilities") or [])
            if "wash_task" not in capabilities:
                capability_violation = True

    return invalid_task, invalid_agent, capability_violation


def inspect_prediction_validity(
    pred_obj: Optional[Dict[str, Any]],
    *,
    assignment_map: Dict[str, Dict[str, Any]],
    task_map: Dict[Any, Dict[str, Any]],
    agent_map: Dict[str, Dict[str, Any]],
) -> Dict[str, Any]:
    result = {
        "invalid_assignment": False,
        "invalid_task": False,
        "invalid_agent": False,
        "capability_violation": False,
        "invalid_candidate_ids": [],
    }
    if pred_obj is None:
        return result

    predicted_ids = normalize_candidate_ids(pred_obj.get("selected_candidate_ids"))
    invalid_candidate_ids: List[str] = []

    for candidate_id in predicted_ids:
        assignment = assignment_map.get(candidate_id)
        if assignment is None:
            invalid_candidate_ids.append(candidate_id)
            continue

        task_id = _normalize_int_like(assignment.get("task_id"))
        agent_name = str(assignment.get("agent") or "").strip()

        if task_id not in task_map:
            result["invalid_task"] = True
        if not agent_name or agent_name not in agent_map:
            result["invalid_agent"] = True

        if agent_name and agent_name in agent_map:
            capabilities = set(agent_map[agent_name].get("capabilities") or [])
            required_capability = infer_required_capability(assignment, task_map.get(task_id))
            if required_capability and required_capability not in capabilities:
                result["capability_violation"] = True

    if invalid_candidate_ids:
        result["invalid_assignment"] = True
        result["invalid_candidate_ids"] = invalid_candidate_ids

    wash_invalid_task, wash_invalid_agent, wash_capability_violation = inspect_wash_assignment(
        pred_obj.get("wash_assignment"),
        agent_map=agent_map,
        task_map=task_map,
    )
    result["invalid_task"] = result["invalid_task"] or wash_invalid_task
    result["invalid_agent"] = result["invalid_agent"] or wash_invalid_agent
    result["capability_violation"] = result["capability_violation"] or wash_capability_violation
    return result


def load_model_and_tokenizer(args: argparse.Namespace):
    tokenizer = AutoTokenizer.from_pretrained(args.model, trust_remote_code=True)
    tokenizer.padding_side = "left"
    if tokenizer.pad_token is None:
        tokenizer.pad_token = tokenizer.eos_token

    model_path = args.model
    is_adapter = (model_path / "adapter_config.json").exists() or (model_path / "adapter_model.safetensors").exists()
    if is_adapter:
        if not args.base_model:
            raise SystemExit("Detected LoRA adapter directory; please pass --base-model.")
        base = AutoModelForCausalLM.from_pretrained(
            args.base_model,
            trust_remote_code=True,
            torch_dtype="auto",
            device_map="auto" if args.device == "cuda" else None,
        )
        try:
            from peft import PeftModel
        except Exception as exc:
            raise SystemExit("peft is required to load LoRA adapters.") from exc
        model = PeftModel.from_pretrained(base, model_path)
    else:
        model = AutoModelForCausalLM.from_pretrained(
            model_path,
            trust_remote_code=True,
            torch_dtype="auto",
            device_map="auto" if args.device == "cuda" else None,
        )

    model.eval()
    return model, tokenizer


def apply_chat_template(tokenizer, system_prompt: str, user_prompt: str, add_generation_prompt: bool) -> str:
    messages: List[Dict[str, str]] = []
    if system_prompt:
        messages.append({"role": "system", "content": system_prompt})
    messages.append({"role": "user", "content": user_prompt})
    return tokenizer.apply_chat_template(messages, tokenize=False, add_generation_prompt=add_generation_prompt)


def build_loss_tensors(
    tokenizer,
    record: Dict[str, Any],
    max_length: int,
) -> Tuple[torch.Tensor, torch.Tensor, torch.Tensor, int]:
    system_prompt = record.get("system") or ""
    user_prompt = record.get("prompt") or ""
    assistant_ref = record.get("response") or ""

    prompt_text = apply_chat_template(tokenizer, system_prompt, user_prompt, add_generation_prompt=True)
    full_text = tokenizer.apply_chat_template(
        [
            *([{"role": "system", "content": system_prompt}] if system_prompt else []),
            {"role": "user", "content": user_prompt},
            {"role": "assistant", "content": assistant_ref},
        ],
        tokenize=False,
        add_generation_prompt=False,
    )

    prompt_ids = tokenizer(prompt_text, add_special_tokens=False).input_ids
    full_ids = tokenizer(full_text, add_special_tokens=False).input_ids

    if len(full_ids) > max_length:
        overflow = len(full_ids) - max_length
        full_ids = full_ids[overflow:]
        prompt_len = max(0, len(prompt_ids) - overflow)
    else:
        prompt_len = len(prompt_ids)

    labels = full_ids.copy()
    for i in range(min(prompt_len, len(labels))):
        labels[i] = -100

    target_tokens = sum(1 for value in labels if value != -100)
    input_ids = torch.tensor([full_ids], dtype=torch.long)
    attention_mask = torch.ones_like(input_ids)
    label_tensor = torch.tensor([labels], dtype=torch.long)
    return input_ids, attention_mask, label_tensor, target_tokens


def compute_loss_metrics(
    model,
    tokenizer,
    records: Iterable[Dict[str, Any]],
    device: torch.device,
    max_length: int,
) -> Tuple[float, float]:
    total_nll = 0.0
    total_tokens = 0

    for record in records:
        input_ids, attention_mask, labels, target_tokens = build_loss_tensors(tokenizer, record, max_length)
        if target_tokens == 0:
            continue
        input_ids = input_ids.to(device)
        attention_mask = attention_mask.to(device)
        labels = labels.to(device)
        with torch.no_grad():
            output = model(input_ids=input_ids, attention_mask=attention_mask, labels=labels)
        total_nll += float(output.loss) * target_tokens
        total_tokens += target_tokens

    mean_loss = total_nll / max(1, total_tokens)
    perplexity = math.exp(mean_loss) if mean_loss < 50 else float("inf")
    return mean_loss, perplexity


def generate_one(
    model,
    tokenizer,
    record: Dict[str, Any],
    device: torch.device,
    max_new_tokens: int,
    temperature: float,
    top_p: float,
) -> str:
    prompt_text = apply_chat_template(
        tokenizer,
        record.get("system") or "",
        record.get("prompt") or "",
        add_generation_prompt=True,
    )
    inputs = tokenizer(prompt_text, return_tensors="pt", add_special_tokens=False)
    inputs = {k: v.to(device) for k, v in inputs.items()}
    do_sample = temperature > 1e-6

    generate_kwargs = dict(
        **inputs,
        max_new_tokens=max_new_tokens,
        do_sample=do_sample,
        pad_token_id=tokenizer.eos_token_id,
    )
    if do_sample:
        generate_kwargs["temperature"] = temperature
        generate_kwargs["top_p"] = top_p

    with torch.no_grad():
        output_ids = model.generate(**generate_kwargs)

    prompt_len = inputs["input_ids"].shape[1]
    return tokenizer.decode(output_ids[0][prompt_len:], skip_special_tokens=True).strip()


def evaluate_generation(records: List[Dict[str, Any]], generations: List[str]) -> Tuple[Dict[str, Any], List[Dict[str, Any]]]:
    rows: List[Dict[str, Any]] = []
    parsed_ok = 0
    exact_json_match = 0
    candidate_exact_match = 0
    candidate_relaxed_match = 0
    candidate_slot_match = 0
    wash_exact_match = 0
    notes_nonempty = 0
    required_keys_ok = 0
    composite_score_total = 0.0
    invalid_assignment_count = 0
    invalid_task_count = 0
    invalid_agent_count = 0
    capability_violation_count = 0

    for idx, (record, pred_text) in enumerate(zip(records, generations)):
        ref_obj, ref_error = try_parse_json_object(record.get("response") or "")
        pred_obj, pred_error = try_parse_json_object(pred_text)

        ref_candidate_ids = normalize_candidate_ids(ref_obj.get("selected_candidate_ids") if ref_obj else None)
        pred_candidate_ids = normalize_candidate_ids(pred_obj.get("selected_candidate_ids") if pred_obj else None)
        assignment_map = build_assignment_map(record)
        task_map = build_task_map(record)
        agent_map = build_agent_map(record)
        ref_candidate_relaxed = normalize_signatures(
            candidate_relaxed_signature(cid, assignment_map) for cid in ref_candidate_ids
        )
        pred_candidate_relaxed = normalize_signatures(
            candidate_relaxed_signature(cid, assignment_map) for cid in pred_candidate_ids
        )
        ref_candidate_slots = normalize_signatures(
            candidate_slot_signature(cid, assignment_map) for cid in ref_candidate_ids
        )
        pred_candidate_slots = normalize_signatures(
            candidate_slot_signature(cid, assignment_map) for cid in pred_candidate_ids
        )
        ref_wash = normalize_wash_assignment(ref_obj.get("wash_assignment") if ref_obj else None)
        pred_wash = normalize_wash_assignment(pred_obj.get("wash_assignment") if pred_obj else None)
        pred_notes = ""
        if pred_obj is not None:
            pred_notes = str(pred_obj.get("notes", "")).strip()

        pred_has_required_keys = False
        if pred_obj is not None:
            pred_has_required_keys = all(
                key in pred_obj for key in ("selected_candidate_ids", "wash_assignment", "notes")
            )

        if pred_obj is not None:
            parsed_ok += 1
        if pred_obj == ref_obj and pred_obj is not None:
            exact_json_match += 1
        if pred_candidate_ids == ref_candidate_ids:
            candidate_exact_match += 1
        if pred_candidate_relaxed == ref_candidate_relaxed:
            candidate_relaxed_match += 1
        if pred_candidate_slots == ref_candidate_slots:
            candidate_slot_match += 1
        if pred_wash == ref_wash:
            wash_exact_match += 1
        if pred_notes:
            notes_nonempty += 1
        if pred_has_required_keys:
            required_keys_ok += 1

        candidate_slot_match_value = int(pred_candidate_slots == ref_candidate_slots)
        wash_exact_match_value = int(pred_wash == ref_wash)
        composite_match_score = 0.5 * candidate_slot_match_value + 0.5 * wash_exact_match_value
        composite_score_total += composite_match_score

        validity = inspect_prediction_validity(
            pred_obj,
            assignment_map=assignment_map,
            task_map=task_map,
            agent_map=agent_map,
        )
        if validity["invalid_assignment"]:
            invalid_assignment_count += 1
        if validity["invalid_task"]:
            invalid_task_count += 1
        if validity["invalid_agent"]:
            invalid_agent_count += 1
        if validity["capability_violation"]:
            capability_violation_count += 1

        meta = record.get("meta") if isinstance(record.get("meta"), dict) else {}
        rows.append(
            {
                "sample_index": idx,
                "split": meta.get("split", ""),
                "pair": meta.get("pair", ""),
                "run_id": meta.get("run_id", ""),
                "tasks": "|".join(meta.get("tasks", [])) if isinstance(meta.get("tasks"), list) else str(meta.get("tasks", "")),
                "trigger": meta.get("trigger", ""),
                "parsed_json": int(pred_obj is not None),
                "required_keys_ok": int(pred_has_required_keys),
                "candidate_ids_exact_match": int(pred_candidate_ids == ref_candidate_ids),
                "candidate_ids_relaxed_match": int(pred_candidate_relaxed == ref_candidate_relaxed),
                "candidate_slot_match": candidate_slot_match_value,
                "wash_assignment_exact_match": wash_exact_match_value,
                "composite_scheduler_match_score": composite_match_score,
                "exact_json_match": int(pred_obj == ref_obj and pred_obj is not None),
                "notes_nonempty": int(bool(pred_notes)),
                "invalid_assignment": int(validity["invalid_assignment"]),
                "invalid_task": int(validity["invalid_task"]),
                "invalid_agent": int(validity["invalid_agent"]),
                "capability_violation": int(validity["capability_violation"]),
                "parse_error": pred_error or "",
                "reference_parse_error": ref_error or "",
                "invalid_candidate_ids": json.dumps(validity["invalid_candidate_ids"], ensure_ascii=False),
                "reference_candidate_ids": json.dumps(ref_candidate_ids, ensure_ascii=False),
                "predicted_candidate_ids": json.dumps(pred_candidate_ids, ensure_ascii=False),
                "reference_candidate_relaxed": json.dumps(ref_candidate_relaxed, ensure_ascii=False),
                "predicted_candidate_relaxed": json.dumps(pred_candidate_relaxed, ensure_ascii=False),
                "reference_candidate_slots": json.dumps(ref_candidate_slots, ensure_ascii=False),
                "predicted_candidate_slots": json.dumps(pred_candidate_slots, ensure_ascii=False),
                "reference_wash_assignment": ref_wash,
                "predicted_wash_assignment": pred_wash,
                "reference_response": record.get("response", ""),
                "predicted_response": pred_text,
            }
        )

    total = max(1, len(rows))
    summary = {
        "num_samples": len(rows),
        "json_parse_pass_rate": parsed_ok / total,
        "required_keys_pass_rate": required_keys_ok / total,
        "candidate_ids_exact_match_rate": candidate_exact_match / total,
        "candidate_ids_relaxed_match_rate": candidate_relaxed_match / total,
        "candidate_slot_match_rate": candidate_slot_match / total,
        "wash_assignment_exact_match_rate": wash_exact_match / total,
        "composite_scheduler_match_score": composite_score_total / total,
        "task_allocation_match_rate": candidate_slot_match / total,
        "wash_task_match_rate": wash_exact_match / total,
        "overall_match_score": composite_score_total / total,
        "exact_json_match_rate": exact_json_match / total,
        "notes_nonempty_rate": notes_nonempty / total,
        "invalid_assignment_rate": invalid_assignment_count / total,
        "invalid_task_rate": invalid_task_count / total,
        "invalid_agent_rate": invalid_agent_count / total,
        "capability_violation_rate": capability_violation_count / total,
    }
    return summary, rows


def write_csv(path: Path, rows: List[Dict[str, Any]]) -> None:
    if not rows:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = list(rows[0].keys())
    with path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def write_jsonl(path: Path, rows: List[Dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")


def main() -> None:
    args = parse_args()
    records = load_jsonl(args.data, args.max_samples)
    if not records:
        raise SystemExit(f"No records loaded from {args.data}")

    args.output_dir.mkdir(parents=True, exist_ok=True)
    model, tokenizer = load_model_and_tokenizer(args)

    device = torch.device("cpu")
    if args.device == "cuda" and torch.cuda.is_available():
        device = next(model.parameters()).device

    mean_loss, perplexity = compute_loss_metrics(model, tokenizer, records, device, args.max_length)

    generations: List[str] = []
    for record in records:
        generations.append(
            generate_one(
                model=model,
                tokenizer=tokenizer,
                record=record,
                device=device,
                max_new_tokens=args.max_new_tokens,
                temperature=args.temperature,
                top_p=args.top_p,
            )
        )

    generation_summary, rows = evaluate_generation(records, generations)

    meta0 = records[0].get("meta") if isinstance(records[0].get("meta"), dict) else {}
    summary = {
        "model": str(args.model),
        "base_model": str(args.base_model) if args.base_model else None,
        "data": str(args.data),
        "num_samples": len(records),
        "split": meta0.get("split", ""),
        "training_target": meta0.get("training_target", ""),
        "teacher_forcing_loss": mean_loss,
        "teacher_forcing_perplexity": perplexity,
        **generation_summary,
    }

    summary_path = args.output_dir / "summary.json"
    csv_path = args.output_dir / "per_sample_report.csv"
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    write_csv(csv_path, rows)

    if args.save_jsonl:
        write_jsonl(args.output_dir / "detailed_results.jsonl", rows)

    print(json.dumps(summary, ensure_ascii=False, indent=2))
    print(f"[eval] Wrote summary to {summary_path}")
    print(f"[eval] Wrote per-sample CSV to {csv_path}")
    if args.save_jsonl:
        print(f"[eval] Wrote detailed JSONL to {args.output_dir / 'detailed_results.jsonl'}")


if __name__ == "__main__":
    main()
