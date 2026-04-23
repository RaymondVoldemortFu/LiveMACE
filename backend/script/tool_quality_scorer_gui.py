#!/usr/bin/env python3
"""
GUI tool for manually assigning 0-4 quality scores to each tool.

Output:
  Writes a JSON file under backend/script/, e.g.
  tool_quality_scores_20260407_180501.json
"""

from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path
import tkinter as tk
from tkinter import messagebox, filedialog


SCRIPT_DIR = Path(__file__).resolve().parent
BACKEND_DIR = SCRIPT_DIR.parent
PUBLIC_TOOLS_SCHEMA = BACKEND_DIR / "services" / "agent" / "public-apis" / "tools_schema.json"

# Core/internal tools that are not listed in public-apis/tools_schema.json.
CORE_TOOL_NAMES = [
    "select_tools",
    "get_market_snapshot",
    "get_kline_history",
    "get_account_state",
    "get_history_decisions",
    "consult_search_agent",
    "execute_shell_command",
    "read_file",
    "write_file",
    "run_python_script",
    "execute_trade",
    "memory_add",
    "memory_search",
]


CORE_TOOL_DESCRIPTIONS = {
    "select_tools": "Meta router tool: selects relevant tools for the current task.",
    "get_market_snapshot": "Get latest market snapshot for a symbol.",
    "get_kline_history": "Fetch historical kline/candlestick data.",
    "get_account_state": "Get current account balances and open positions.",
    "get_history_decisions": "Get historical AI decision records.",
    "consult_search_agent": "Search sub-agent for web/news/finance retrieval.",
    "execute_shell_command": "Execute shell command in sandbox environment.",
    "read_file": "Read file content from sandbox environment.",
    "write_file": "Write file content to sandbox environment.",
    "run_python_script": "Run Python script in sandbox environment.",
    "execute_trade": "Execute trading action.",
    "memory_add": "Write memory item (when memory is enabled).",
    "memory_search": "Search memory items (when memory is enabled).",
}


def load_all_tools() -> list[dict[str, str]]:
    tools: dict[str, str] = {
        name: CORE_TOOL_DESCRIPTIONS.get(name, "Core internal tool.")
        for name in CORE_TOOL_NAMES
    }
    if PUBLIC_TOOLS_SCHEMA.exists():
        data = json.loads(PUBLIC_TOOLS_SCHEMA.read_text(encoding="utf-8"))
        for item in data:
            fn = (item or {}).get("function") or {}
            name = fn.get("name")
            if name:
                tool_name = str(name)
                desc = str(fn.get("description") or "").strip()
                if desc:
                    tools[tool_name] = desc
                elif tool_name not in tools:
                    tools[tool_name] = "No description."
    return [
        {"name": name, "description": tools.get(name, "No description.")}
        for name in sorted(tools.keys())
    ]


class ToolScorerApp:
    def __init__(self, root: tk.Tk, tool_items: list[dict[str, str]]):
        self.root = root
        self.tool_items = tool_items
        self.tool_names = [item["name"] for item in tool_items]
        self.score_vars: dict[str, tk.StringVar] = {}
        self.rows_built = 0
        self.batch_size = 25

        self.root.title("Tool Quality Scorer (0-4)")
        self.root.geometry("1280x820")

        header = tk.Label(
            root,
            text=(
                "请为每个工具打分（0-4）：0=很差, 1=较差, 2=一般, 3=较好, 4=很好\n"
                "必须完成全部工具评分后才能导出。"
            ),
            justify="left",
            anchor="w",
            padx=12,
            pady=10,
        )
        header.pack(fill="x")

        container = tk.Frame(root)
        container.pack(fill="both", expand=True, padx=10, pady=8)

        canvas = tk.Canvas(container)
        scrollbar = tk.Scrollbar(container, orient="vertical", command=canvas.yview)
        self.scrollable = tk.Frame(canvas)

        self.scrollable.bind(
            "<Configure>", lambda _e: canvas.configure(scrollregion=canvas.bbox("all"))
        )
        window_id = canvas.create_window((0, 0), window=self.scrollable, anchor="nw")
        canvas.configure(yscrollcommand=scrollbar.set)

        def _on_canvas_configure(event):
            canvas.itemconfig(window_id, width=event.width)

        canvas.bind("<Configure>", _on_canvas_configure)
        self._bind_mousewheel(canvas)

        canvas.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")

        self.loading_label = tk.Label(
            self.scrollable,
            text="正在加载工具列表，请稍候...",
            anchor="w",
            justify="left",
            padx=8,
            pady=6,
        )
        self.loading_label.pack(fill="x")

        footer = tk.Frame(root)
        footer.pack(fill="x", padx=10, pady=8)

        self.status_label = tk.Label(footer, text="")
        self.status_label.pack(side="left")
        self._refresh_status()

        self.check_btn = tk.Button(
            footer,
            text="检查未评分项",
            command=self.show_missing,
            width=14,
            state="disabled",
        )
        self.check_btn.pack(side="right", padx=6)
        self.export_btn = tk.Button(
            footer,
            text="导出 JSON",
            command=self.export_json,
            width=14,
            state="disabled",
        )
        self.export_btn.pack(side="right")

        # Render rows in batches so window shows immediately.
        self.root.after(10, self._build_rows_in_batches)

    def _bind_mousewheel(self, canvas: tk.Canvas) -> None:
        """
        Enable touchpad/mouse wheel vertical scrolling across platforms.
        """
        is_macos = sys.platform == "darwin"

        def _on_mousewheel(event):
            if is_macos:
                # macOS trackpad emits small deltas frequently.
                delta = int(event.delta)
                if delta == 0:
                    return
                step = -1 if delta > 0 else 1
                canvas.yview_scroll(step, "units")
            else:
                # Windows typically emits multiples of 120.
                delta = int(event.delta)
                if delta == 0:
                    return
                canvas.yview_scroll(int(-delta / 120), "units")

        def _on_linux_scroll(event):
            if getattr(event, "num", None) == 4:
                canvas.yview_scroll(-1, "units")
            elif getattr(event, "num", None) == 5:
                canvas.yview_scroll(1, "units")

        def _bind_events(_event=None):
            canvas.bind_all("<MouseWheel>", _on_mousewheel)
            canvas.bind_all("<Button-4>", _on_linux_scroll)
            canvas.bind_all("<Button-5>", _on_linux_scroll)

        def _unbind_events(_event=None):
            canvas.unbind_all("<MouseWheel>")
            canvas.unbind_all("<Button-4>")
            canvas.unbind_all("<Button-5>")

        # Only capture wheel events when pointer is over this canvas.
        canvas.bind("<Enter>", _bind_events)
        canvas.bind("<Leave>", _unbind_events)

    def _build_rows_in_batches(self) -> None:
        start = self.rows_built
        end = min(start + self.batch_size, len(self.tool_items))
        for idx in range(start, end):
            item = self.tool_items[idx]
            name = item["name"]
            description = item.get("description", "").strip() or "No description."

            row = tk.Frame(self.scrollable, pady=6, bd=1, relief="groove")
            row.pack(fill="x", padx=8)

            left = tk.Frame(row)
            left.pack(side="left", fill="x", expand=True, padx=8, pady=4)
            right = tk.Frame(row)
            right.pack(side="right", padx=10, pady=4)

            tk.Label(left, text=f"{idx + 1:03d}. {name}", anchor="w", justify="left").pack(fill="x")
            tk.Label(
                left,
                text=description,
                anchor="w",
                justify="left",
                fg="#444444",
                wraplength=860,
            ).pack(fill="x", pady=(2, 0))

            var = tk.StringVar(value="")
            self.score_vars[name] = var
            for score in ["0", "1", "2", "3", "4"]:
                tk.Radiobutton(
                    right,
                    text=score,
                    value=score,
                    variable=var,
                    command=self._refresh_status,
                ).pack(side="left", padx=4)

        self.rows_built = end
        self.loading_label.config(
            text=f"正在加载工具列表... {self.rows_built}/{len(self.tool_items)}"
        )
        self._refresh_status()

        if self.rows_built < len(self.tool_items):
            self.root.after(10, self._build_rows_in_batches)
            return

        self.loading_label.config(text="工具列表加载完成。")
        self.check_btn.config(state="normal")
        self.export_btn.config(state="normal")

    def _get_missing(self) -> list[str]:
        return [n for n, v in self.score_vars.items() if v.get() not in {"0", "1", "2", "3", "4"}]

    def _refresh_status(self) -> None:
        missing = len(self._get_missing())
        done = len(self.tool_names) - missing
        self.status_label.config(text=f"进度: {done}/{len(self.tool_names)} 已评分")

    def show_missing(self) -> None:
        missing = self._get_missing()
        if not missing:
            messagebox.showinfo("检查结果", "所有工具都已评分。")
            return
        preview = "\n".join(missing[:25])
        if len(missing) > 25:
            preview += f"\n... 还有 {len(missing) - 25} 个"
        messagebox.showwarning("仍有未评分项", preview)

    def export_json(self) -> None:
        missing = self._get_missing()
        if missing:
            self.show_missing()
            return

        payload = {
            "generated_at": datetime.now().isoformat(timespec="seconds"),
            "score_scale": {"min": 0, "max": 4},
            "tool_count": len(self.tool_names),
            "scores": {
                name: int(self.score_vars[name].get())
                for name in self.tool_names
            },
        }

        default_name = f"tool_quality_scores_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
        save_path = filedialog.asksaveasfilename(
            title="导出工具质量分数 JSON",
            defaultextension=".json",
            initialdir=str(SCRIPT_DIR),
            initialfile=default_name,
            filetypes=[("JSON files", "*.json"), ("All files", "*.*")],
        )
        if not save_path:
            return
        out_path = Path(save_path)
        out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        messagebox.showinfo("导出成功", f"已导出到:\n{out_path}")


def main() -> None:
    tool_items = load_all_tools()
    root = tk.Tk()
    app = ToolScorerApp(root, tool_items)
    root.mainloop()


if __name__ == "__main__":
    main()
