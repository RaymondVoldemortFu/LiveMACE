import argparse
import shutil
from datetime import datetime
from pathlib import Path
from tkinter import Tk, ttk, messagebox


class ToolEnvPrunerApp:
    def __init__(self, root: Tk, toolenv_root: Path):
        self.root = root
        self.toolenv_root = toolenv_root.resolve()
        self.tools_root = self.toolenv_root / "tools"
        self.response_root = self.toolenv_root / "response_examples"

        self.tool_keys = []
        self.tool_paths = {}
        self.selected = set()

        self._build_ui()
        self._load_tree()

    def _build_ui(self):
        self.root.title("ToolEnv 剪枝工具")
        self.root.geometry("900x600")

        top_frame = ttk.Frame(self.root)
        top_frame.pack(fill="x", padx=10, pady=8)

        self.stats_label = ttk.Label(top_frame, text="Selected 0 / 0")
        self.stats_label.pack(side="left")

        ttk.Button(top_frame, text="全选", command=self._select_all).pack(side="right", padx=4)
        ttk.Button(top_frame, text="全不选", command=self._select_none).pack(side="right", padx=4)
        ttk.Button(top_frame, text="应用(移除未选)", command=self._apply_prune).pack(side="right", padx=4)

        tree_frame = ttk.Frame(self.root)
        tree_frame.pack(fill="both", expand=True, padx=10, pady=8)

        self.tree = ttk.Treeview(tree_frame, show="tree")
        self.tree.pack(side="left", fill="both", expand=True)
        self.tree.bind("<Button-1>", self._on_click)
        self.tree.bind("<space>", self._on_space)

        scrollbar = ttk.Scrollbar(tree_frame, orient="vertical", command=self.tree.yview)
        scrollbar.pack(side="right", fill="y")
        self.tree.configure(yscrollcommand=scrollbar.set)

    def _load_tree(self):
        self.tree.delete(*self.tree.get_children())
        self.tool_keys.clear()
        self.tool_paths.clear()
        self.selected.clear()

        if not self.tools_root.exists():
            messagebox.showerror("错误", f"tools 目录不存在: {self.tools_root}")
            self._update_stats()
            return

        for category_dir in sorted([p for p in self.tools_root.iterdir() if p.is_dir()]):
            cat_id = f"cat::{category_dir.name}"
            self.tree.insert("", "end", iid=cat_id, text=f"[x] {category_dir.name}")
            for json_file in sorted(category_dir.glob("*.json")):
                tool_name = json_file.stem
                tool_key = (category_dir.name, tool_name)
                self.tool_keys.append(tool_key)
                self.selected.add(tool_key)

                tool_dir = category_dir / tool_name
                response_file = self.response_root / category_dir.name / f"{tool_name}.json"
                self.tool_paths[tool_key] = {
                    "json": json_file,
                    "dir": tool_dir,
                    "response": response_file,
                }

                tool_id = f"tool::{category_dir.name}::{tool_name}"
                self.tree.insert(cat_id, "end", iid=tool_id, text=f"[x] {tool_name}")

        self._update_stats()

    def _update_stats(self):
        self.stats_label.config(text=f"Selected {len(self.selected)} / {len(self.tool_keys)}")

    def _toggle_item(self, item_id: str):
        if item_id.startswith("cat::"):
            category = item_id.split("::", 1)[1]
            children = self.tree.get_children(item_id)
            all_selected = all(self._is_tool_selected(self._tool_key_from_id(c)) for c in children)
            target_selected = not all_selected
            for child in children:
                self._set_tool_selected(child, target_selected)
            self._set_category_label(item_id)
        elif item_id.startswith("tool::"):
            self._set_tool_selected(item_id, not self._is_tool_selected(self._tool_key_from_id(item_id)))
            parent = self.tree.parent(item_id)
            if parent:
                self._set_category_label(parent)
        self._update_stats()

    def _on_click(self, event):
        region = self.tree.identify_region(event.x, event.y)
        element = self.tree.identify("element", event.x, event.y)
        if element == "Treeitem.indicator":
            return
        if region not in ("tree", "cell"):
            return
        item_id = self.tree.identify_row(event.y)
        if item_id:
            self._toggle_item(item_id)

    def _on_space(self, _event):
        item_id = self.tree.focus()
        if item_id:
            self._toggle_item(item_id)

    def _tool_key_from_id(self, item_id: str):
        _, category, tool = item_id.split("::", 2)
        return category, tool

    def _is_tool_selected(self, tool_key):
        return tool_key in self.selected

    def _set_tool_selected(self, item_id: str, is_selected: bool):
        tool_key = self._tool_key_from_id(item_id)
        if is_selected:
            self.selected.add(tool_key)
            self.tree.item(item_id, text=f"[x] {tool_key[1]}")
        else:
            self.selected.discard(tool_key)
            self.tree.item(item_id, text=f"[ ] {tool_key[1]}")

    def _set_category_label(self, cat_id: str):
        children = self.tree.get_children(cat_id)
        if not children:
            return
        all_selected = all(self._is_tool_selected(self._tool_key_from_id(c)) for c in children)
        any_selected = any(self._is_tool_selected(self._tool_key_from_id(c)) for c in children)
        if all_selected:
            label = "[x]"
        elif any_selected:
            label = "[-]"
        else:
            label = "[ ]"
        name = cat_id.split("::", 1)[1]
        self.tree.item(cat_id, text=f"{label} {name}")

    def _select_all(self):
        for tool_key in self.tool_keys:
            self.selected.add(tool_key)
        for item_id in self.tree.get_children():
            for child in self.tree.get_children(item_id):
                self.tree.item(child, text=f"[x] {self._tool_key_from_id(child)[1]}")
            self.tree.item(item_id, text=f"[x] {item_id.split('::', 1)[1]}")
        self._update_stats()

    def _select_none(self):
        self.selected.clear()
        for item_id in self.tree.get_children():
            for child in self.tree.get_children(item_id):
                self.tree.item(child, text=f"[ ] {self._tool_key_from_id(child)[1]}")
            self.tree.item(item_id, text=f"[ ] {item_id.split('::', 1)[1]}")
        self._update_stats()

    def _apply_prune(self):
        unselected = [k for k in self.tool_keys if k not in self.selected]
        if not unselected:
            messagebox.showinfo("提示", "没有需要移除的工具。")
            return

        preview = ", ".join([f"{c}/{t}" for c, t in unselected[:10]])
        if len(unselected) > 10:
            preview += " ..."
        confirm = messagebox.askyesno(
            "确认移除",
            f"将从 toolenv 中移除 {len(unselected)} 个工具。\n"
            f"示例: {preview}\n"
            "为保证安全，这些文件会被移动到 backend/.toolenv_pruned_backup/ 下。",
        )
        if not confirm:
            return

        backup_root = (self.toolenv_root.parents[2] / ".toolenv_pruned_backup" /
                       datetime.now().strftime("%Y%m%d_%H%M%S"))
        backup_root.mkdir(parents=True, exist_ok=True)

        moved_count = 0
        for tool_key in unselected:
            paths = self.tool_paths.get(tool_key, {})
            for path in [paths.get("json"), paths.get("dir"), paths.get("response")]:
                if not path:
                    continue
                path = Path(path).resolve()
                if not path.exists():
                    continue
                try:
                    rel = path.relative_to(self.toolenv_root)
                except ValueError:
                    continue
                dest = backup_root / rel
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(path), str(dest))
                moved_count += 1

        messagebox.showinfo(
            "完成",
            f"已移动 {moved_count} 个文件/目录到 {backup_root}。",
        )
        self._load_tree()


def main():
    parser = argparse.ArgumentParser(description="ToolEnv GUI 剪枝工具")
    parser.add_argument(
        "--toolenv-root",
        default="backend/services/agent/toolenv",
        help="ToolEnv 根目录（绝对或相对路径）",
    )
    args = parser.parse_args()

    toolenv_root = Path(args.toolenv_root).resolve()
    root = Tk()
    ToolEnvPrunerApp(root, toolenv_root)
    root.mainloop()


if __name__ == "__main__":
    main()

