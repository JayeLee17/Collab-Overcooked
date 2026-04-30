#!/usr/bin/env python3
from __future__ import annotations

import csv
from pathlib import Path
from statistics import mean


ROOT = Path(__file__).resolve().parents[1]
OUT_DIR = ROOT / "docs" / "figures"
CSV_PATH = OUT_DIR / "scheduler_comparison_summary.csv"

SCHEDULER_ORDER = ["No Scheduler", "Rule Scheduler", "LLM Scheduler"]
SCHEDULER_SHORT = {
    "No Scheduler": "No",
    "Rule Scheduler": "Rule",
    "LLM Scheduler": "LLM",
}
COLORS = {
    "No Scheduler": "#4C6A92",
    "Rule Scheduler": "#C97A40",
    "LLM Scheduler": "#4C9A64",
}
DEFAULT_DATA = [
    ("E1", "No Scheduler", 2.00, 0.3143),
    ("E1", "Rule Scheduler", 2.00, 0.3524),
    ("E1", "LLM Scheduler", 2.00, 0.3500),
    ("E2", "No Scheduler", 1.67, 0.3721),
    ("E2", "Rule Scheduler", 2.00, 0.3846),
    ("E2", "LLM Scheduler", 2.00, 0.3850),
    ("E3", "No Scheduler", 1.33, 0.3500),
    ("E3", "Rule Scheduler", 1.67, 0.3500),
    ("E3", "LLM Scheduler", 2.00, 0.3661),
    ("E4", "No Scheduler", 1.60, 0.4478),
    ("E4", "Rule Scheduler", 2.33, 0.4944),
    ("E4", "LLM Scheduler", 3.00, 0.4278),
    ("E5", "No Scheduler", 2.00, 0.3771),
    ("E5", "Rule Scheduler", 2.67, 0.4069),
    ("E5", "LLM Scheduler", 3.00, 0.4154),
    ("E6", "No Scheduler", 2.00, 0.4467),
    ("E6", "Rule Scheduler", 2.00, 0.4422),
    ("E6", "LLM Scheduler", 2.33, 0.4400),
    ("E7", "No Scheduler", 2.33, 0.4850),
    ("E7", "Rule Scheduler", 2.67, 0.4875),
    ("E7", "LLM Scheduler", 3.33, 0.4275),
    ("E8", "No Scheduler", 2.00, 0.5180),
    ("E8", "Rule Scheduler", 2.00, 0.4740),
    ("E8", "LLM Scheduler", 3.00, 0.4160),
    ("E9", "No Scheduler", 1.33, 0.5750),
    ("E9", "Rule Scheduler", 1.67, 0.5783),
    ("E9", "LLM Scheduler", 2.00, 0.5950),
]


def build_records() -> dict[str, dict[str, dict[str, float]]]:
    records: dict[str, dict[str, dict[str, float]]] = {}
    for scene, scheduler, tasks, util in DEFAULT_DATA:
        records.setdefault(scene, {})[scheduler] = {
            "tasks": tasks,
            "util": util,
        }
    return records


def load_records_from_csv(csv_path: Path) -> dict[str, dict[str, dict[str, float]]]:
    records: dict[str, dict[str, dict[str, float]]] = {}
    with csv_path.open("r", encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            scene = row["Scene"].strip()
            scheduler = row["Scheduler"].strip()
            records.setdefault(scene, {})[scheduler] = {
                "tasks": float(row["Avg Completed Tasks"]),
                "util": float(row["Agent Utilization"]),
            }
    return records


def format_num(value: float, digits: int = 2) -> str:
    return f"{value:.{digits}f}"


def write_csv(records: dict[str, dict[str, dict[str, float]]]) -> Path:
    out_path = OUT_DIR / "scheduler_comparison_summary.csv"
    with out_path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["Scene", "Scheduler", "Avg Completed Tasks", "Agent Utilization"])
        for scene in sorted(records):
            for scheduler in SCHEDULER_ORDER:
                row = records[scene][scheduler]
                writer.writerow(
                    [
                        scene,
                        scheduler,
                        format_num(row["tasks"], 2),
                        format_num(row["util"], 4),
                    ]
                )
    return out_path


def scheduler_averages(records: dict[str, dict[str, dict[str, float]]]) -> dict[str, dict[str, float]]:
    summary = {}
    for scheduler in SCHEDULER_ORDER:
        tasks = [records[scene][scheduler]["tasks"] for scene in sorted(records)]
        util = [records[scene][scheduler]["util"] for scene in sorted(records)]
        summary[scheduler] = {"tasks": mean(tasks), "util": mean(util)}
    return summary


def scene_task_winners(records: dict[str, dict[str, dict[str, float]]]) -> dict[str, list[str]]:
    winners = {}
    for scene in sorted(records):
        best = max(records[scene][scheduler]["tasks"] for scheduler in SCHEDULER_ORDER)
        winners[scene] = [
            scheduler for scheduler in SCHEDULER_ORDER if records[scene][scheduler]["tasks"] == best
        ]
    return winners


def write_markdown_table(records: dict[str, dict[str, dict[str, float]]]) -> str:
    lines = [
        "| 场景 | 调度器 | 三轮实验平均完成任务数 | Agent Utilization |",
        "| --- | --- | ---: | ---: |",
    ]
    for scene in sorted(records):
        for scheduler in SCHEDULER_ORDER:
            row = records[scene][scheduler]
            lines.append(
                f"| {scene} | {scheduler} | {format_num(row['tasks'], 2)} | {format_num(row['util'], 4)} |"
            )
    return "\n".join(lines)


def write_aggregated_table(summary: dict[str, dict[str, float]]) -> str:
    base_tasks = summary["No Scheduler"]["tasks"]
    base_util = summary["No Scheduler"]["util"]
    lines = [
        "| 调度器 | 场景平均完成任务数 | 相对 No Scheduler 提升 | 平均 Agent Utilization |",
        "| --- | ---: | ---: | ---: |",
    ]
    for scheduler in SCHEDULER_ORDER:
        tasks = summary[scheduler]["tasks"]
        util = summary[scheduler]["util"]
        uplift = 0.0 if scheduler == "No Scheduler" else (tasks - base_tasks) / base_tasks * 100
        uplift_text = "-" if scheduler == "No Scheduler" else f"{uplift:.2f}%"
        lines.append(
            f"| {scheduler} | {tasks:.2f} | {uplift_text} | {util:.4f} |"
        )
    return "\n".join(lines)


def write_analysis_md(records: dict[str, dict[str, dict[str, float]]]) -> Path:
    summary = scheduler_averages(records)
    winners = scene_task_winners(records)
    llm_win_count = sum("LLM Scheduler" in wins and len(wins) == 1 for wins in winners.values())
    llm_nonloss_count = sum("LLM Scheduler" in wins for wins in winners.values())
    rule_better_than_no = sum(
        records[scene]["Rule Scheduler"]["tasks"] > records[scene]["No Scheduler"]["tasks"]
        for scene in sorted(records)
    )
    e4_e9_llm = mean(records[scene]["LLM Scheduler"]["tasks"] for scene in ["E4", "E5", "E6", "E7", "E8", "E9"])
    e4_e9_no = mean(records[scene]["No Scheduler"]["tasks"] for scene in ["E4", "E5", "E6", "E7", "E8", "E9"])
    improvement_hard = (e4_e9_llm - e4_e9_no) / e4_e9_no * 100

    out_path = OUT_DIR / "scheduler_comparison_section_4_5_1.md"
    full_table = write_markdown_table(records)
    aggregated_table = write_aggregated_table(summary)

    body = f"""## 4.5.1 全局调度器对比实验

为验证全局调度机制对多智能体协作烹饪任务的影响，本文在 E1-E9 共 9 个实验场景中，对 `No Scheduler`、`Rule Scheduler` 和 `LLM Scheduler` 三种调度方式进行了对比。每个场景均重复运行 3 次，并以三轮实验的平均完成任务数与平均 `Agent Utilization` 作为主要评价指标。其中，平均完成任务数用于衡量系统在给定时间预算内的整体产出能力，`Agent Utilization` 用于刻画智能体动作时间中非空等候行为的占比，从而反映调度策略对群体协作节奏的影响。

表 4-? 给出了三种调度器在各场景中的平均结果。从整体趋势来看，`LLM Scheduler` 在任务完成数指标上表现最佳，9 个场景的平均完成任务数达到 {summary["LLM Scheduler"]["tasks"]:.2f}，显著高于 `No Scheduler` 的 {summary["No Scheduler"]["tasks"]:.2f} 和 `Rule Scheduler` 的 {summary["Rule Scheduler"]["tasks"]:.2f}。相较于无调度基线，`Rule Scheduler` 的平均完成任务数提升了 {(summary["Rule Scheduler"]["tasks"] - summary["No Scheduler"]["tasks"]) / summary["No Scheduler"]["tasks"] * 100:.2f}%，而 `LLM Scheduler` 的提升达到 {(summary["LLM Scheduler"]["tasks"] - summary["No Scheduler"]["tasks"]) / summary["No Scheduler"]["tasks"] * 100:.2f}%。相较于规则调度器，`LLM Scheduler` 进一步提升了 {(summary["LLM Scheduler"]["tasks"] - summary["Rule Scheduler"]["tasks"]) / summary["Rule Scheduler"]["tasks"] * 100:.2f}%。

分场景分析可以看出，在相对简单的 E1 场景中，三种调度方式的平均完成任务数均为 2，说明在低复杂度任务下，系统即使不引入额外调度也能够维持基本稳定的协作效率。但从 E2、E3 开始，调度策略的差异逐渐显现：`Rule Scheduler` 相比 `No Scheduler` 能在部分场景中提供一定改善，说明显式规则在缓解简单冲突和任务分配失衡方面具有积极作用；然而该优势仍然有限。在 E4-E9 这些任务链更长、资源竞争更明显或重复订单更多的场景中，`LLM Scheduler` 的优势更加稳定，其在该组场景中的平均完成任务数达到 {e4_e9_llm:.2f}，相比 `No Scheduler` 的 {e4_e9_no:.2f} 提升了 {improvement_hard:.2f}%。

进一步观察逐场景结果可知，`LLM Scheduler` 在 9 个场景中有 {llm_nonloss_count} 个场景达到最高完成任务数，其中 {llm_win_count} 个场景为单独最优。在 E4、E5、E7、E8 和 E9 等中高复杂度场景中，其优势尤为明显。例如在 E4 场景中，`LLM Scheduler` 的平均完成任务数达到 3.00，高于 `Rule Scheduler` 的 {records["E4"]["Rule Scheduler"]["tasks"]:.2f} 和 `No Scheduler` 的 {records["E4"]["No Scheduler"]["tasks"]:.2f}；在 E7 场景中，`LLM Scheduler` 达到 {records["E7"]["LLM Scheduler"]["tasks"]:.2f}，分别比 `Rule Scheduler` 和 `No Scheduler` 多完成 {records["E7"]["LLM Scheduler"]["tasks"] - records["E7"]["Rule Scheduler"]["tasks"]:.2f} 和 {records["E7"]["LLM Scheduler"]["tasks"] - records["E7"]["No Scheduler"]["tasks"]:.2f} 个任务；在 E8 场景中，虽然无调度与规则调度均只能完成 {records["E8"]["No Scheduler"]["tasks"]:.0f} 个任务，但 `LLM Scheduler` 可提升至 {records["E8"]["LLM Scheduler"]["tasks"]:.0f} 个任务。这说明基于大模型的调度器能够结合全局任务状态、资源占用情况与智能体能力分工，更有效地决定任务的保留、释放与重分配时机，从而提升复杂协作环境中的整体产出。

`Agent Utilization` 指标则揭示了一个更细致的现象。三种调度器在该指标上的差距并不像完成任务数那样显著，`Rule Scheduler` 的平均利用率最高，为 {summary["Rule Scheduler"]["util"]:.4f}，`No Scheduler` 和 `LLM Scheduler` 分别为 {summary["No Scheduler"]["util"]:.4f} 和 {summary["LLM Scheduler"]["util"]:.4f}。这表明更高的利用率并不必然对应更高的任务完成数。尤其是在 E7、E8 等场景中，`No Scheduler` 或 `Rule Scheduler` 的利用率数值更高，但最终完成任务数却低于 `LLM Scheduler`。其原因在于，利用率只能反映智能体是否处于“忙碌”状态，却无法区分这些动作是否真正服务于关键任务链。相比之下，`LLM Scheduler` 虽然没有在所有场景中保持最高利用率，但能够将有限动作预算更集中地投入到关键路径推进上，因此在最终完成任务数上取得更优结果。

综合以上结果可以认为，三种调度器形成了较清晰的性能层次：`No Scheduler` 作为基线方法，在简单场景下已能支持基本协作，但在复杂场景中容易因缺乏全局协调而造成资源竞争和任务推进失衡；`Rule Scheduler` 通过人工规则在一定程度上改善了任务分配效率，在 9 个场景中有 {rule_better_than_no} 个场景优于无调度基线，但其规则表达能力有限，面对复杂依赖关系和动态冲突时仍难以稳定获得最优结果；`LLM Scheduler` 则在大多数场景中取得了最高或并列最高的完成任务数，尤其在中高复杂度任务中展现出更强的全局规划与协作组织能力。因此，本文后续实验将以 `LLM Scheduler` 作为主要调度方案，并将 `Rule Scheduler` 作为可解释的规则基线，进一步分析不同环境结构和异构智能体配置下的调度表现。

### 表 4-? 全局调度器对比实验结果

{full_table}

### 表 4-? 三种调度器总体平均表现

{aggregated_table}

### 图 4-? 不同调度器在各场景中的平均完成任务数

![不同调度器在各场景中的平均完成任务数](./scheduler_comparison_tasks.svg)

### 图 4-? 不同调度器在各场景中的 Agent Utilization

![不同调度器在各场景中的 Agent Utilization](./scheduler_comparison_utilization.svg)
"""
    out_path.write_text(body, encoding="utf-8")
    return out_path


def svg_rect(x, y, width, height, fill, stroke=None, stroke_width=1.0, opacity=1.0) -> str:
    attrs = [
        f'x="{x:.1f}"',
        f'y="{y:.1f}"',
        f'width="{width:.1f}"',
        f'height="{height:.1f}"',
        f'fill="{fill}"',
        f'fill-opacity="{opacity:.3f}"',
    ]
    if stroke:
        attrs.append(f'stroke="{stroke}"')
        attrs.append(f'stroke-width="{stroke_width:.1f}"')
    return f"<rect {' '.join(attrs)} />"


def svg_line(x1, y1, x2, y2, color, width=1.0, dash=None) -> str:
    attrs = [
        f'x1="{x1:.1f}"',
        f'y1="{y1:.1f}"',
        f'x2="{x2:.1f}"',
        f'y2="{y2:.1f}"',
        f'stroke="{color}"',
        f'stroke-width="{width:.1f}"',
    ]
    if dash:
        attrs.append(f'stroke-dasharray="{dash}"')
    return f"<line {' '.join(attrs)} />"


def svg_text(x, y, text, size=12, fill="#222222", anchor="start", weight="normal") -> str:
    safe = (
        str(text)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )
    return (
        f'<text x="{x:.1f}" y="{y:.1f}" font-size="{size}" fill="{fill}" '
        f'text-anchor="{anchor}" font-family="Arial, Helvetica, sans-serif" font-weight="{weight}">{safe}</text>'
    )


def render_grouped_bar_svg(
    records: dict[str, dict[str, dict[str, float]]],
    metric: str,
    title: str,
    y_label: str,
    out_path: Path,
    y_max_override: float | None = None,
    decimals: int = 2,
) -> None:
    scenes = sorted(records)
    width = 1240
    height = 560
    left = 92
    right = 42
    top = 88
    bottom = 88
    plot_w = width - left - right
    plot_h = height - top - bottom

    values = [records[scene][scheduler][metric] for scene in scenes for scheduler in SCHEDULER_ORDER]
    max_value = max(values)
    y_max = y_max_override if y_max_override is not None else max_value * 1.18

    bg = "#FFFFFF"
    panel = "#FFFFFF"
    grid = "#D6CCBE"
    text = "#2E2A26"
    muted = "#6E655B"

    group_step = plot_w / len(scenes)
    bar_width = group_step * 0.20
    x_offsets = [-bar_width - 7, 0, bar_width + 7]

    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">',
        svg_rect(0, 0, width, height, bg),
        svg_rect(left, top, plot_w, plot_h, panel, stroke="#DCCFBE", stroke_width=1.0),
        svg_text(width / 2, 36, title, size=24, fill=text, anchor="middle", weight="bold"),
    ]

    tick_count = 5
    for i in range(tick_count + 1):
        value = y_max * i / tick_count
        y = top + plot_h - (value / y_max) * plot_h
        parts.append(svg_line(left, y, left + plot_w, y, grid, width=1.0, dash="4,6"))
        parts.append(svg_text(left - 10, y + 4, f"{value:.{decimals}f}", size=11, fill=muted, anchor="end"))

    parts.append(svg_text(30, top + plot_h / 2, y_label, size=13, fill=text, anchor="middle"))

    for idx, scene in enumerate(scenes):
        group_center = left + group_step * (idx + 0.5)
        for mode_idx, scheduler in enumerate(SCHEDULER_ORDER):
            value = records[scene][scheduler][metric]
            bar_h = (value / y_max) * plot_h if y_max > 0 else 0
            x = group_center + x_offsets[mode_idx] - bar_width / 2
            y = top + plot_h - bar_h
            parts.append(
                svg_rect(
                    x,
                    y,
                    bar_width,
                    max(bar_h, 1.0),
                    COLORS[scheduler],
                    stroke=COLORS[scheduler],
                    stroke_width=1.1,
                    opacity=0.92,
                )
            )
            parts.append(
                svg_text(
                    x + bar_width / 2,
                    y - 8,
                    f"{value:.{decimals}f}",
                    size=10,
                    fill=COLORS[scheduler],
                    anchor="middle",
                )
            )
        parts.append(svg_text(group_center, top + plot_h + 28, scene, size=12, fill=text, anchor="middle", weight="bold"))

    parts.append(svg_line(left, top + plot_h, left + plot_w, top + plot_h, "#82796E", width=1.2))
    parts.append(svg_line(left, top, left, top + plot_h, "#82796E", width=1.2))

    legend_x = left + plot_w - 220
    legend_y = 78
    for idx, scheduler in enumerate(SCHEDULER_ORDER):
        yy = legend_y + idx * 24
        parts.append(svg_rect(legend_x, yy - 10, 16, 16, COLORS[scheduler], stroke=COLORS[scheduler], stroke_width=1.0))
        parts.append(svg_text(legend_x + 24, yy + 3, scheduler, size=12, fill=text))

    parts.append(svg_text(width / 2, height - 20, "Scenes", size=13, fill=text, anchor="middle"))
    parts.append("</svg>")
    out_path.write_text("\n".join(parts), encoding="utf-8")


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    if CSV_PATH.exists():
        records = load_records_from_csv(CSV_PATH)
    else:
        records = build_records()
        write_csv(records)
    write_analysis_md(records)
    render_grouped_bar_svg(
        records,
        metric="tasks",
        title="Average Completed Tasks Across Scheduler Settings",
        y_label="Avg Completed Tasks",
        out_path=OUT_DIR / "scheduler_comparison_tasks.svg",
        y_max_override=3.8,
        decimals=2,
    )
    render_grouped_bar_svg(
        records,
        metric="util",
        title="Agent Utilization Across Scheduler Settings",
        y_label="Agent Utilization",
        out_path=OUT_DIR / "scheduler_comparison_utilization.svg",
        y_max_override=0.65,
        decimals=3,
    )
    print(f"Generated assets in {OUT_DIR}")


if __name__ == "__main__":
    main()
