#!/usr/bin/env python3
"""
Summarize Collab-Overcooked experiment results into CSV + Markdown.

This script scans `results/**/experiment_*.json` produced by `collab_overcooked.main`,
extracts multi-agent / multi-task metrics, and writes:
  - results/summary.csv
  - results/summary.md

Usage:
  python scripts/summarize_experiments.py
  python scripts/summarize_experiments.py --results-root results --out-dir results
  python scripts/summarize_experiments.py --glob "results/**/experiment_*.json"
"""

from __future__ import annotations

import argparse
import csv
import glob
import json
import os
import re
import statistics
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple


def _safe_div(a: float, b: float) -> Optional[float]:
    if b == 0:
        return None
    return a / b


def _mean(xs: List[float]) -> Optional[float]:
    xs = [x for x in xs if x is not None]
    if not xs:
        return None
    return float(statistics.mean(xs))


def _stdev(xs: List[float]) -> Optional[float]:
    xs = [x for x in xs if x is not None]
    if len(xs) < 2:
        return None
    return float(statistics.pstdev(xs))


def _infer_order_name(order_list: List[str]) -> str:
    if not order_list:
        return "unknown"
    if len(order_list) <= 3:
        return "_".join(order_list)
    return f"{order_list[0]}_x{len(order_list)}"


def _infer_run_id_from_folder(folder_name: str, order_name: str) -> str:
    suffix = "_" + order_name
    if folder_name.endswith(suffix):
        return folder_name[: -len(suffix)]
    # fallback: return folder name as run_id if cannot parse
    return folder_name


def _parse_exp_id(run_id: str) -> Optional[str]:
    m = re.match(r"^(E\\d+)_", run_id)
    return m.group(1) if m else None


def _parse_mode(run_id: str) -> Optional[str]:
    # run_id like: E1_llm_T35 / E1_rule_T35 / E1_off_T35
    m = re.match(r"^E\\d+_(off|rule|llm)_T\\d+", run_id)
    return m.group(1) if m else None


def _count_waits_from_content(content: Any) -> Tuple[int, int]:
    """
    Returns (wait_actions, total_actions) from `statistics_dict["content"]`.

    Each timestep entry is expected to have:
      {"actions": [[...],[...],...]}  # per-agent list of action strings
    """
    wait_cnt = 0
    total = 0
    if not isinstance(content, list):
        return 0, 0
    for step in content:
        actions = step.get("actions") if isinstance(step, dict) else None
        if not isinstance(actions, list):
            continue
        for per_agent in actions:
            if not isinstance(per_agent, list):
                continue
            for a in per_agent:
                if not isinstance(a, str):
                    continue
                total += 1
                if a.lower().startswith("wait("):
                    wait_cnt += 1
    return wait_cnt, total


def _summarize_a2a(a2a_log: Any) -> Dict[str, Any]:
    if not isinstance(a2a_log, list):
        return {
            "a2a_total_messages": 0,
            "a2a_request": 0,
            "a2a_inform": 0,
            "a2a_propose": 0,
            "a2a_accept": 0,
            "a2a_reject": 0,
        }

    total_messages = 0
    type_counts = {"REQUEST": 0, "INFORM": 0, "PROPOSE": 0, "ACCEPT": 0, "REJECT": 0}
    for agent_entry in a2a_log:
        if not isinstance(agent_entry, dict):
            continue
        total_messages += int(agent_entry.get("total_messages", 0) or 0)
        hist = agent_entry.get("message_history", [])
        if not isinstance(hist, list):
            continue
        for msg in hist:
            if not isinstance(msg, dict):
                continue
            t = str(msg.get("type", "")).upper()
            if t in type_counts:
                type_counts[t] += 1
    return {
        "a2a_total_messages": total_messages,
        "a2a_request": type_counts["REQUEST"],
        "a2a_inform": type_counts["INFORM"],
        "a2a_propose": type_counts["PROPOSE"],
        "a2a_accept": type_counts["ACCEPT"],
        "a2a_reject": type_counts["REJECT"],
    }


def _summarize_scheduler(gs: Any) -> Dict[str, Any]:
    if not isinstance(gs, dict):
        return {
            "gs_enabled": None,
            "gs_timeout_steps": None,
            "gs_clean_dish_threshold": None,
            "gs_model": None,
            "gs_log_steps": 0,
            "gs_trigger_count": 0,
            "gs_llm_used_count": 0,
            "gs_fallback_count": 0,
            "gs_outgoing_total": 0,
            "gs_timeout_release_count": 0,
        }

    logs = gs.get("logs", []) or []
    trigger_count = 0
    llm_used_count = 0
    fallback_count = 0
    outgoing_total = 0
    timeout_release_count = 0

    if isinstance(logs, list):
        for step in logs:
            if not isinstance(step, dict):
                continue
            if step.get("trigger") is not None:
                trigger_count += 1
            if step.get("llm_used") is True:
                llm_used_count += 1
            outgoing_total += int(step.get("outgoing_count", 0) or 0)
            events = step.get("events", []) or []
            if isinstance(events, list):
                for ev in events:
                    evs = str(ev)
                    if "[LLM->fallback]" in evs:
                        fallback_count += 1
                    if "timeout" in evs and "release" in evs:
                        timeout_release_count += 1

    return {
        "gs_enabled": gs.get("enabled"),
        "gs_timeout_steps": gs.get("timeout_steps"),
        "gs_clean_dish_threshold": gs.get("clean_dish_threshold"),
        "gs_model": gs.get("model"),
        "gs_log_steps": len(logs) if isinstance(logs, list) else 0,
        "gs_trigger_count": trigger_count,
        "gs_llm_used_count": llm_used_count,
        "gs_fallback_count": fallback_count,
        "gs_outgoing_total": outgoing_total,
        "gs_timeout_release_count": timeout_release_count,
    }


@dataclass
class Row:
    path: str
    run_dir: str
    run_id: str
    exp_id: Optional[str]
    mode: Optional[str]
    T: int
    num_agents: int
    num_orders: int
    orders: str
    completed: int
    total_score: float
    score_per_step: Optional[float]
    wait_ratio: Optional[float]
    # scheduler
    gs_enabled: Any
    gs_model: Any
    gs_trigger_count: int
    gs_llm_used_count: int
    gs_fallback_count: int
    gs_outgoing_total: int
    gs_timeout_release_count: int
    # a2a
    a2a_total_messages: int
    a2a_request: int
    a2a_inform: int
    a2a_propose: int
    a2a_accept: int
    a2a_reject: int

    def as_dict(self) -> Dict[str, Any]:
        return self.__dict__.copy()


def load_one(path: str) -> Row:
    p = Path(path)
    run_dir = p.parent.name
    with p.open("r", encoding="utf-8") as f:
        d = json.load(f)

    # order list: use first timestep's order_list if present, else fall back to finished list
    first_orders: List[str] = []
    content = d.get("content", [])
    if isinstance(content, list) and content and isinstance(content[0], dict):
        first_orders = content[0].get("order_list", []) or []
    if not first_orders:
        # fall back
        first_orders = d.get("total_order_finished", []) or []

    order_name = _infer_order_name(first_orders if isinstance(first_orders, list) else [])
    run_id = _infer_run_id_from_folder(run_dir, order_name)
    exp_id = _parse_exp_id(run_id)
    mode = _parse_mode(run_id)

    # steps
    timestamps = d.get("total_timestamp", []) or []
    T = len(timestamps) if isinstance(timestamps, list) else 0

    # agents
    num_agents = 0
    if isinstance(content, list) and content:
        actions = content[0].get("actions") if isinstance(content[0], dict) else None
        if isinstance(actions, list):
            num_agents = len(actions)

    # wait ratio
    wait_cnt, total_cnt = _count_waits_from_content(content)
    wait_ratio = _safe_div(wait_cnt, total_cnt) if total_cnt else None

    # completion & score
    completed = len(d.get("total_order_finished", []) or [])
    total_score = float(d.get("total_score", 0) or 0)
    score_per_step = _safe_div(total_score, T) if T else None

    # scheduler summary
    gs_sum = _summarize_scheduler(d.get("global_scheduler", {}))
    # a2a summary
    a2a_sum = _summarize_a2a(d.get("a2a_protocol_log", []))

    orders_str = ",".join(first_orders) if isinstance(first_orders, list) else str(first_orders)

    return Row(
        path=str(p),
        run_dir=run_dir,
        run_id=run_id,
        exp_id=exp_id,
        mode=mode,
        T=T,
        num_agents=num_agents,
        num_orders=len(first_orders) if isinstance(first_orders, list) else 0,
        orders=orders_str,
        completed=completed,
        total_score=total_score,
        score_per_step=score_per_step,
        wait_ratio=wait_ratio,
        gs_enabled=gs_sum["gs_enabled"],
        gs_model=gs_sum["gs_model"],
        gs_trigger_count=gs_sum["gs_trigger_count"],
        gs_llm_used_count=gs_sum["gs_llm_used_count"],
        gs_fallback_count=gs_sum["gs_fallback_count"],
        gs_outgoing_total=gs_sum["gs_outgoing_total"],
        gs_timeout_release_count=gs_sum["gs_timeout_release_count"],
        a2a_total_messages=a2a_sum["a2a_total_messages"],
        a2a_request=a2a_sum["a2a_request"],
        a2a_inform=a2a_sum["a2a_inform"],
        a2a_propose=a2a_sum["a2a_propose"],
        a2a_accept=a2a_sum["a2a_accept"],
        a2a_reject=a2a_sum["a2a_reject"],
    )


def write_csv(rows: List[Row], out_path: str) -> None:
    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = list(rows[0].as_dict().keys()) if rows else []
    with out.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        for r in rows:
            w.writerow(r.as_dict())


def _fmt(x: Any) -> str:
    if x is None:
        return "-"
    if isinstance(x, float):
        return f"{x:.4f}"
    return str(x)


def write_md(rows: List[Row], out_path: str) -> None:
    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)

    # Group by exp_id then mode
    grouped: Dict[str, Dict[str, List[Row]]] = {}
    for r in rows:
        k = r.exp_id or "unknown"
        m = r.mode or "unknown"
        grouped.setdefault(k, {}).setdefault(m, []).append(r)

    def agg(rs: List[Row]) -> Dict[str, Any]:
        return {
            "n": len(rs),
            "completed_mean": _mean([r.completed for r in rs]),
            "score_mean": _mean([r.total_score for r in rs]),
            "score_per_step_mean": _mean([r.score_per_step for r in rs]),
            "wait_ratio_mean": _mean([r.wait_ratio for r in rs]),
            "a2a_msgs_mean": _mean([r.a2a_total_messages for r in rs]),
            "gs_triggers_mean": _mean([r.gs_trigger_count for r in rs]),
            "gs_llm_used_mean": _mean([r.gs_llm_used_count for r in rs]),
            "gs_fallback_mean": _mean([r.gs_fallback_count for r in rs]),
        }

    lines: List[str] = []
    lines.append("## Experiment summary (aggregated)\n")
    lines.append("Each cell is the mean over matching runs.\n")
    lines.append("| Exp | Mode | n | completed | score | score/step | wait_ratio | a2a_msgs | gs_triggers | llm_used | llm_fallback |")
    lines.append("|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|")
    for exp_id in sorted(grouped.keys(), key=lambda s: (len(s), s)):
        for mode in ["off", "rule", "llm", "unknown"]:
            if mode not in grouped[exp_id]:
                continue
            a = agg(grouped[exp_id][mode])
            lines.append(
                "| "
                + " | ".join(
                    [
                        exp_id,
                        mode,
                        str(a["n"]),
                        _fmt(a["completed_mean"]),
                        _fmt(a["score_mean"]),
                        _fmt(a["score_per_step_mean"]),
                        _fmt(a["wait_ratio_mean"]),
                        _fmt(a["a2a_msgs_mean"]),
                        _fmt(a["gs_triggers_mean"]),
                        _fmt(a["gs_llm_used_mean"]),
                        _fmt(a["gs_fallback_mean"]),
                    ]
                )
                + " |"
            )

    lines.append("\n## Per-run details\n")
    lines.append("| run_id | mode | T | agents | orders | completed | score | score/step | wait_ratio | a2a_msgs | gs_triggers | llm_used | llm_fallback | result_path |")
    lines.append("|---|---:|---:|---:|---|---:|---:|---:|---:|---:|---:|---:|---:|---|")
    for r in sorted(rows, key=lambda x: (x.exp_id or "Z", x.mode or "Z", x.run_id)):
        lines.append(
            "| "
            + " | ".join(
                [
                    r.run_id,
                    r.mode or "-",
                    str(r.T),
                    str(r.num_agents),
                    r.orders,
                    str(r.completed),
                    _fmt(r.total_score),
                    _fmt(r.score_per_step),
                    _fmt(r.wait_ratio),
                    str(r.a2a_total_messages),
                    str(r.gs_trigger_count),
                    str(r.gs_llm_used_count),
                    str(r.gs_fallback_count),
                    r.path,
                ]
            )
            + " |"
        )

    out.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="Summarize experiment JSON logs to CSV + MD")
    parser.add_argument("--results-root", type=str, default="results", help="Results root directory")
    parser.add_argument("--glob", dest="glob_pat", type=str, default="", help="Override file glob pattern")
    parser.add_argument("--out-dir", type=str, default="results", help="Output directory")
    args = parser.parse_args()

    if args.glob_pat:
        pat = args.glob_pat
    else:
        pat = os.path.join(args.results_root, "**", "experiment_*.json")

    paths = sorted(glob.glob(pat, recursive=True))
    if not paths:
        print(f"No files matched: {pat}")
        return 1

    rows: List[Row] = []
    for p in paths:
        try:
            rows.append(load_one(p))
        except Exception as e:
            print(f"[WARN] failed to parse {p}: {e}")

    if not rows:
        print("No valid experiment files parsed.")
        return 1

    out_dir = Path(args.out_dir)
    csv_path = str(out_dir / "summary.csv")
    md_path = str(out_dir / "summary.md")
    write_csv(rows, csv_path)
    write_md(rows, md_path)
    print(f"Wrote: {csv_path}")
    print(f"Wrote: {md_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

