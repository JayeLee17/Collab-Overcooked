#!/usr/bin/env python3
"""
Generate a self-contained HTML map viewer from an Overcooked layout file.

Usage:
  python scripts/map_viewer_generator.py
  python scripts/map_viewer_generator.py --layout dependencies/overcooked_ai/overcooked_ai_py/data/layouts/multi_agent_map.layout
  python scripts/map_viewer_generator.py --layout path/to/layout.layout --output map.html

Opens the generated HTML in the browser, or save to file. Style matches the reference:
- Beige walkable paths, dark brown walls/stations
- Icons for pot, oven, chopping board, blender, water, dish dispenser, serving star, ingredients
- Chef markers at spawn positions
"""

import argparse
import json
import os
import sys
from pathlib import Path

# Layout symbols -> display (type, label, css class)
SYMBOL_MAP = {
    "X": ("wall", "", "cell-wall"),
    " ": ("floor", "", "cell-floor"),
    "P": ("pot", "P", "cell-pot"),
    "O": ("oven", "O", "cell-oven"),
    "C": ("chopping_board", "C", "cell-chopping"),
    "B": ("blender", "B", "cell-blender"),
    "G": ("grill", "G", "cell-grill"),
    "H": ("steamer", "H", "cell-steamer"),
    "K": ("prep_table", "K", "cell-prep"),
    "M": ("mixer", "M", "cell-mixer"),
    "W": ("water", "W", "cell-water"),
    "D": ("dish_dispenser", "D", "cell-dish"),
    "S": ("serving", "★", "cell-serving"),
    "I": ("ingredient", "I", "cell-ingredient"),
}


def parse_layout(layout_path: str) -> dict:
    """Load layout (Python dict format with ''' or \"\"\" strings) and return grid matrix + agent positions."""
    path = Path(layout_path)
    if not path.exists():
        raise FileNotFoundError(f"Layout not found: {path}")

    with open(path, "r", encoding="utf-8") as f:
        raw = f.read()
    # .layout files use Python literals (e.g. """ for grid), not strict JSON
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        data = eval(raw)

    grid_str = data["grid"]
    rows = grid_str.splitlines()
    while rows and rows[0] == "":
        rows.pop(0)
    while rows and rows[-1] == "":
        rows.pop()
    if not rows:
        raise ValueError("Empty grid")

    first_row = rows[0].lstrip(" ")
    remaining_rows = rows[1:]
    nonempty_remaining = [row for row in remaining_rows if row.strip()]
    if nonempty_remaining:
        shared_indent = min(
            len(row) - len(row.lstrip(" "))
            for row in nonempty_remaining
        )
        remaining_rows = [
            row[shared_indent:] if len(row) >= shared_indent else ""
            for row in remaining_rows
        ]
    rows = [first_row] + remaining_rows

    grid = []
    agents = []  # list of { "id": 0..n, "x": col, "y": row }

    for y, row in enumerate(rows):
        line = []
        for x, c in enumerate(row):
            if c in "123456789":
                line.append({"type": "floor", "symbol": " ", "class": "cell-floor"})
                agents.append({"id": int(c), "x": x, "y": y})
            else:
                entry = SYMBOL_MAP.get(c, ("floor", c, "cell-floor"))
                line.append({
                    "type": entry[0],
                    "symbol": entry[1] or c,
                    "class": entry[2],
                })
        grid.append(line)

    return {
        "grid": grid,
        "agents": agents,
        "height": len(grid),
        "width": max((len(row) for row in grid), default=0),
    }


def build_html(data: dict, title: str = "Overcooked Map") -> str:
    """Build a single HTML file with embedded data and CSS/JS."""
    grid_json = json.dumps(data)

    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>{title}</title>
  <style>
    * {{ box-sizing: border-box; }}
    body {{ margin: 20px; font-family: sans-serif; background: #f5f0e6; }}
    h1 {{ margin-bottom: 12px; color: #333; }}
    .map-container {{ display: inline-block; padding: 8px; background: #4a3728; border-radius: 8px; box-shadow: 0 4px 12px rgba(0,0,0,0.2); }}
    .map-grid {{ display: flex; flex-direction: column; gap: 2px; border: 2px solid #3d2e22; border-radius: 4px; overflow: hidden; align-items: flex-start; }}
    .map-row {{ display: flex; gap: 2px; }}
    .cell {{ width: 48px; height: 48px; display: flex; align-items: center; justify-content: center; font-weight: bold; font-size: 18px; color: #333; }}
    .cell-wall {{ background: #5c4033; }}
    .cell-floor {{ background: #e8dcc4; }}
    .cell-pot {{ background: #8b7355; color: #fff; }}
    .cell-oven {{ background: #6b4423; color: #f4d03f; }}
    .cell-chopping {{ background: #7d5a3c; color: #fff; }}
    .cell-blender {{ background: #6b4423; color: #ddd; }}
    .cell-grill {{ background: #8a4b2a; color: #ffd27f; }}
    .cell-steamer {{ background: #6d7d8c; color: #ffffff; }}
    .cell-prep {{ background: #8d6e63; color: #fff; }}
    .cell-mixer {{ background: #5f4b8b; color: #efe6ff; }}
    .cell-water {{ background: #4a6fa5; color: #fff; }}
    .cell-dish {{ background: #8b7355; color: #fff; }}
    .cell-serving {{ background: #3d2e22; color: #f4d03f; font-size: 24px; }}
    .cell-ingredient {{ background: #7d5a3c; color: #c0392b; }}
    .cell-agent {{ background: #e8dcc4; position: relative; }}
    .agent-marker {{ width: 36px; height: 36px; border-radius: 50%; display: flex; align-items: center; justify-content: center; font-size: 14px; font-weight: bold; color: #333; box-shadow: 0 2px 4px rgba(0,0,0,0.3); }}
    .agent-0 {{ background: #85c1e9; }}
    .agent-1 {{ background: #f5b7b1; }}
    .agent-2 {{ background: #a9dfbf; }}
    .agent-3 {{ background: #f9e79f; }}
    .agent-4 {{ background: #aed6e0; }}
    .legend {{ margin-top: 16px; display: flex; flex-wrap: wrap; gap: 12px; font-size: 13px; color: #555; }}
    .legend span {{ display: inline-flex; align-items: center; gap: 6px; }}
    .legend-box {{ width: 20px; height: 20px; border-radius: 3px; }}
  </style>
</head>
<body>
  <h1>{title}</h1>
  <div class="map-container">
    <div id="map" class="map-grid"></div>
  </div>
  <div class="legend" id="legend"></div>

  <script>
    const DATA = {grid_json};

    function render() {{
      const {{ grid, agents }} = DATA;
      const mapEl = document.getElementById('map');
      const legendEl = document.getElementById('legend');
      mapEl.innerHTML = '';

      const agentAt = {{}};
      agents.forEach(a => {{
        const key = a.y + ',' + a.x;
        if (!agentAt[key]) agentAt[key] = [];
        agentAt[key].push(a);
      }});

      for (let y = 0; y < grid.length; y++) {{
        const rowEl = document.createElement('div');
        rowEl.className = 'map-row';
        for (let x = 0; x < grid[y].length; x++) {{
          const cell = grid[y][x];
          const key = y + ',' + x;
          const as = agentAt[key] || [];
          const div = document.createElement('div');
          div.className = 'cell ' + cell.class + (as.length ? ' cell-agent' : '');
          if (as.length > 0) {{
            const a = as[0];
            const m = document.createElement('div');
            m.className = 'agent-marker agent-' + (a.id % 5);
            m.textContent = 'A' + a.id;
            div.appendChild(m);
          }} else {{
            div.textContent = cell.symbol || '';
          }}
          rowEl.appendChild(div);
        }}
        mapEl.appendChild(rowEl);
      }}

      const items = [
        ['cell-floor', '可行走'],
        ['cell-wall', '墙'],
        ['cell-pot', '锅 P'],
        ['cell-oven', '烤箱 O'],
        ['cell-chopping', '砧板 C'],
        ['cell-blender', '搅拌机 B'],
        ['cell-grill', '烤架 G'],
        ['cell-steamer', '蒸锅 H'],
        ['cell-prep', '备菜台 K'],
        ['cell-mixer', '混合台 M'],
        ['cell-water', '水池 W'],
        ['cell-dish', '盘子 D'],
        ['cell-serving', '出菜 ★'],
        ['cell-ingredient', '食材 I'],
      ];
      legendEl.innerHTML = items.map(([cls, label]) =>
        '<span><span class="legend-box ' + cls + '"></span>' + label + '</span>'
      ).join('');
    }}

    render();
  </script>
</body>
</html>
"""


def main():
    parser = argparse.ArgumentParser(description="Generate HTML map viewer from Overcooked layout")
    root = Path(__file__).resolve().parent.parent
    default_layout = root / "dependencies/overcooked_ai/overcooked_ai_py/data/layouts/multi_agent_map.layout"
    parser.add_argument("--layout", "-l", type=str, default=str(default_layout), help="Path to .layout JSON file")
    parser.add_argument("--output", "-o", type=str, default="", help="Output HTML path (default: map_viewer.html in project root)")
    parser.add_argument("--no-open", action="store_true", help="Do not open the HTML in browser")
    args = parser.parse_args()

    try:
        data = parse_layout(args.layout)
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)

    layout_name = Path(args.layout).stem
    title = f"Map: {layout_name}"
    html = build_html(data, title=title)

    out_path = args.output or str(root / "map_viewer.html")
    Path(out_path).parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"Wrote: {out_path}")

    if not args.no_open:
        try:
            import webbrowser
            webbrowser.open("file://" + os.path.abspath(out_path))
        except Exception:
            print("Could not open browser; open the file manually.")

    return 0


if __name__ == "__main__":
    sys.exit(main())
