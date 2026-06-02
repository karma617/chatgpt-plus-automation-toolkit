from __future__ import annotations

import json
import os
import queue
import re
import subprocess
import sys
import threading
from datetime import datetime, timezone
from pathlib import Path


def _ensure_tk_library_paths() -> None:
    """Some portable Python installs do not auto-discover Tcl/Tk on Windows."""
    if os.name != "nt":
        return
    candidates = [
        Path(getattr(sys, "_MEIPASS", "")) / "tcl",
        Path(sys.base_prefix) / "tcl",
        Path(sys.prefix) / "tcl",
        Path(sys.executable).resolve().parent / "tcl",
        Path(sys.executable).resolve().parent / "_internal" / "tcl",
    ]
    for base in candidates:
        tcl_dir = base / "tcl8.6"
        tk_dir = base / "tk8.6"
        if (tcl_dir / "init.tcl").exists() and (tk_dir / "tk.tcl").exists():
            os.environ.setdefault("TCL_LIBRARY", str(tcl_dir))
            os.environ.setdefault("TK_LIBRARY", str(tk_dir))
            return


_ensure_tk_library_paths()

from tkinter import BOTH, END, LEFT, RIGHT, X, Y, filedialog, messagebox, ttk
import tkinter as tk

import panel_runner
from modules import paypal_flow_state
from control_panel.env_service import get_known_env_fields, read_env, update_env
from control_panel.file_registry import PanelFile, get_panel_files
from control_panel.proxy_tools import add_proxy_schemes
from control_panel.sms_options import dynamic_env_options, parse_dynamic_display
from control_panel.text_pool_service import clear_file, dedupe_lines, export_txt, import_txt, read_text, save_text


EMAIL_RE = re.compile(r"[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}")
ANSI_RE = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")


def _u(value: str) -> str:
    return value.encode("ascii").decode("unicode_escape")


def strip_ansi_for_display(text: str) -> str:
    return ANSI_RE.sub("", text)


def app_root() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    source_root = Path(__file__).resolve().parent
    packaged_root = source_root / "ChatGPTAssistantPanel"
    if not (source_root / "config.yaml").exists() and (packaged_root / "config.yaml").exists():
        return packaged_root
    return source_root


class ResourcePage(ttk.Frame):
    def __init__(self, master: tk.Widget, panel_file: PanelFile):
        super().__init__(master)
        self.panel_file = panel_file
        self._build()
        self.refresh()

    def _build(self) -> None:
        header = ttk.Frame(self)
        header.pack(fill=X, padx=10, pady=(10, 4))
        ttk.Label(header, text=self.panel_file.label, font=("Microsoft YaHei UI", 12, "bold")).pack(side=LEFT)
        ttk.Label(header, text=str(self.panel_file.path), foreground="#555").pack(side=LEFT, padx=(14, 0))

        toolbar = ttk.Frame(self)
        toolbar.pack(fill=X, padx=10, pady=(0, 8))
        ttk.Button(toolbar, text="刷新", command=self.refresh).pack(side=LEFT, padx=2)
        ttk.Button(toolbar, text="保存", command=self.save).pack(side=LEFT, padx=2)
        ttk.Button(toolbar, text="导入 TXT", command=self.import_file).pack(side=LEFT, padx=2)
        ttk.Button(toolbar, text="导出 TXT", command=self.export_file).pack(side=LEFT, padx=2)
        ttk.Button(toolbar, text="去重", command=self.dedupe).pack(side=LEFT, padx=2)
        if self.panel_file.key.startswith("proxy_"):
            ttk.Button(toolbar, text="添加协议头", command=self.add_proxy_scheme_headers).pack(side=LEFT, padx=2)
        ttk.Button(toolbar, text="清空", command=self.clear).pack(side=LEFT, padx=2)

        text_frame = ttk.Frame(self)
        text_frame.pack(fill=BOTH, expand=True, padx=10, pady=(0, 10))
        self.text = tk.Text(text_frame, wrap="none", undo=True)
        y_scroll = ttk.Scrollbar(text_frame, orient="vertical", command=self.text.yview)
        x_scroll = ttk.Scrollbar(text_frame, orient="horizontal", command=self.text.xview)
        self.text.configure(yscrollcommand=y_scroll.set, xscrollcommand=x_scroll.set)
        self.text.grid(row=0, column=0, sticky="nsew")
        y_scroll.grid(row=0, column=1, sticky="ns")
        x_scroll.grid(row=1, column=0, sticky="ew")
        text_frame.columnconfigure(0, weight=1)
        text_frame.rowconfigure(0, weight=1)

    def refresh(self) -> None:
        self.text.delete("1.0", END)
        self.text.insert("1.0", read_text(self.panel_file.path))

    def save(self) -> None:
        save_text(self.panel_file.path, self.text.get("1.0", END).rstrip("\n") + "\n")
        messagebox.showinfo("保存成功", f"已保存到:\n{self.panel_file.path}")

    def import_file(self) -> None:
        path = filedialog.askopenfilename(filetypes=[("Text files", "*.txt"), ("All files", "*.*")])
        if not path:
            return
        content = Path(path).read_text(encoding="utf-8-sig")
        added = import_txt(self.panel_file.path, content, append=True)
        self.refresh()
        messagebox.showinfo("导入完成", f"已导入 {added} 行")

    def export_file(self) -> None:
        path = filedialog.asksaveasfilename(defaultextension=".txt", filetypes=[("Text files", "*.txt")])
        if not path:
            return
        Path(path).write_text(export_txt(self.panel_file.path), encoding="utf-8")
        messagebox.showinfo("导出完成", f"已导出到:\n{path}")

    def dedupe(self) -> None:
        removed = dedupe_lines(self.panel_file.path)
        self.refresh()
        messagebox.showinfo("去重完成", f"已移除 {removed} 行重复内容")

    def add_proxy_scheme_headers(self) -> None:
        current = self.text.get("1.0", END)
        normalized = add_proxy_schemes(current)
        self.text.delete("1.0", END)
        self.text.insert("1.0", normalized)
        total = len([line for line in normalized.splitlines() if line.strip()])
        messagebox.showinfo("处理完成", f"已校准 {total} 行代理，缺失协议头的行已添加 http://")

    def clear(self) -> None:
        if not messagebox.askyesno("确认清空", f"确定清空 {self.panel_file.label} 吗？"):
            return
        clear_file(self.panel_file.path)
        self.refresh()


class ToolTip:
    def __init__(self, widget: tk.Widget, text: str) -> None:
        self.widget = widget
        self.text = text
        self.tip: tk.Toplevel | None = None
        widget.bind("<Enter>", self.show, add="+")
        widget.bind("<Leave>", self.hide, add="+")

    def show(self, _event: tk.Event | None = None) -> None:
        if self.tip or not self.text:
            return
        x = self.widget.winfo_rootx() + 18
        y = self.widget.winfo_rooty() + self.widget.winfo_height() + 6
        self.tip = tk.Toplevel(self.widget)
        self.tip.wm_overrideredirect(True)
        self.tip.wm_geometry(f"+{x}+{y}")
        label = tk.Label(
            self.tip,
            text=self.text,
            justify="left",
            background="#fff7d6",
            relief="solid",
            borderwidth=1,
            wraplength=520,
            padx=8,
            pady=5,
        )
        label.pack()

    def hide(self, _event: tk.Event | None = None) -> None:
        if self.tip:
            self.tip.destroy()
            self.tip = None


class EnvPage(ttk.Frame):
    def __init__(self, master: tk.Widget, env_path: Path):
        super().__init__(master)
        self.env_path = env_path
        self.vars: dict[str, tk.StringVar] = {}
        self.widgets: dict[object, tk.Widget] = {}
        self.sms_refreshing = False
        self.dynamic_option_cache: dict[str, tuple[str, ...]] = {}
        self.dynamic_loading: set[str] = set()
        self.dynamic_post_after_load: set[str] = set()
        self._build()
        self.refresh()

    def _build(self) -> None:
        toolbar = ttk.Frame(self)
        toolbar.pack(fill=X, padx=10, pady=10)
        ttk.Label(toolbar, text=".env 常用配置", font=("Microsoft YaHei UI", 12, "bold")).pack(side=LEFT)
        ttk.Label(toolbar, text=str(self.env_path), foreground="#555").pack(side=LEFT, padx=(14, 0))
        self.sms_refresh_button: ttk.Button | None = None
        ttk.Button(toolbar, text="保存", command=self.save).pack(side=RIGHT, padx=2)
        ttk.Button(toolbar, text="刷新", command=self.refresh).pack(side=RIGHT, padx=2)

        canvas = tk.Canvas(self, highlightthickness=0)
        self.canvas = canvas
        scrollbar = ttk.Scrollbar(self, orient="vertical", command=canvas.yview)
        self.form = ttk.Frame(canvas)
        self.form.bind("<Configure>", lambda _e: canvas.configure(scrollregion=canvas.bbox("all")))
        self.form_window = canvas.create_window((0, 0), window=self.form, anchor="nw")
        canvas.bind("<Configure>", self._resize_form_window)
        canvas.configure(yscrollcommand=scrollbar.set)
        canvas.pack(side=LEFT, fill=BOTH, expand=True, padx=(10, 0), pady=(0, 10))
        scrollbar.pack(side=RIGHT, fill=Y, padx=(0, 10), pady=(0, 10))

        row = 0
        current_group = ""
        for key in get_known_env_fields():
            group = getattr(key, "group", "") or ""
            if group != current_group:
                current_group = group
                group_label = ttk.Label(self.form, text=current_group, font=("Microsoft YaHei UI", 10, "bold"))
                group_label.grid(row=row, column=0, columnspan=2, sticky="ew", padx=6, pady=(10, 4))
                row += 1
            label = ttk.Label(self.form, text=str(key), width=36)
            label.grid(row=row, column=0, sticky="w", padx=6, pady=3)
            tooltip = getattr(key, "tooltip", "")
            if tooltip:
                ToolTip(label, tooltip)
            var = tk.StringVar()
            self.vars[key] = var
            choices = getattr(key, "choices", ())
            if choices:
                widget = ttk.Combobox(self.form, textvariable=var, values=choices, width=86, state="readonly")
            elif self._supports_dynamic_options(key):
                widget = ttk.Combobox(self.form, textvariable=var, values=(), width=86, state="normal")
                widget.bind("<FocusIn>", lambda _e, field=key: self.load_dynamic_options(field), add="+")
                widget.bind("<Button-1>", lambda _e, field=key: self.load_dynamic_options(field, post_after_load=True), add="+")
                widget.bind("<KeyRelease>", lambda _e, field=key: self.filter_dynamic_options(field), add="+")
            else:
                widget = ttk.Entry(self.form, textvariable=var, width=88, show="")
            widget.grid(row=row, column=1, sticky="ew", padx=6, pady=3)
            self.widgets[key] = widget
            row += 1
        self.form.columnconfigure(1, weight=1)
        self._install_mousewheel_bindings()

    def _resize_form_window(self, event: tk.Event) -> None:
        self.canvas.itemconfigure(self.form_window, width=max(1, int(event.width)))
        self.canvas.configure(scrollregion=self.canvas.bbox("all"))

    def _install_mousewheel_bindings(self) -> None:
        for widget in [self, self.canvas, self.form, *self.form.winfo_children()]:
            widget.bind("<Enter>", self._bind_mousewheel, add="+")
            widget.bind("<Leave>", self._unbind_mousewheel, add="+")

    def _bind_mousewheel(self, _event: tk.Event | None = None) -> None:
        self.bind_all("<MouseWheel>", self._on_mousewheel)
        self.bind_all("<Button-4>", self._on_mousewheel)
        self.bind_all("<Button-5>", self._on_mousewheel)

    def _unbind_mousewheel(self, _event: tk.Event | None = None) -> None:
        self.unbind_all("<MouseWheel>")
        self.unbind_all("<Button-4>")
        self.unbind_all("<Button-5>")

    def _on_mousewheel(self, event: tk.Event) -> str:
        if getattr(event, "num", None) == 4:
            delta = -3
        elif getattr(event, "num", None) == 5:
            delta = 3
        else:
            delta = -int(getattr(event, "delta", 0) / 120) or 0
        if delta:
            self.canvas.yview_scroll(delta, "units")
        return "break"

    def refresh(self) -> None:
        values = read_env(self.env_path)
        for key, var in self.vars.items():
            env_key = getattr(key, "key", str(key))
            var.set(values.get(env_key, ""))

    def save(self) -> None:
        update_env(self.env_path, {key: parse_dynamic_display(var.get()) for key, var in self.vars.items()})
        messagebox.showinfo("保存成功", f"已更新:\n{self.env_path}")

    def refresh_sms_options(self) -> None:
        if self.sms_refreshing:
            return
        env_values = {getattr(key, "key", str(key)): parse_dynamic_display(var.get()) for key, var in self.vars.items()}
        env_values.update(read_env(self.env_path))
        targets = [
            (key, getattr(key, "key", str(key)))
            for key, widget in self.widgets.items()
            if self._supports_dynamic_options(key) and isinstance(widget, ttk.Combobox)
        ]
        self.sms_refreshing = True
        if self.sms_refresh_button is not None:
            self.sms_refresh_button.configure(text="刷新中...", state="disabled")

        def worker() -> None:
            results: dict[str, tuple[str, ...]] = {}
            errors: list[str] = []
            for _field, env_key in targets:
                try:
                    options = dynamic_env_options(env_key, env_values)
                except Exception as exc:
                    errors.append(f"{env_key}: {exc}")
                    continue
                if options:
                    results[env_key] = tuple(item.display() for item in options)
            self.after(0, lambda: self._apply_sms_options(results, errors))

        threading.Thread(target=worker, daemon=True).start()

    def _apply_sms_options(self, results: dict[str, tuple[str, ...]], errors: list[str]) -> None:
        updated = 0
        for key, widget in self.widgets.items():
            env_key = getattr(key, "key", str(key))
            values = results.get(env_key)
            if values and isinstance(widget, ttk.Combobox):
                self.dynamic_option_cache[env_key] = values
                widget.configure(values=values)
                updated += 1
        self.sms_refreshing = False
        if self.sms_refresh_button is not None:
            self.sms_refresh_button.configure(text="刷新接码平台选项", state="normal")
        detail = f"已刷新 {updated} 个接码平台下拉项；未配置密钥的平台会跳过。"
        if errors:
            detail += f"\n失败 {len(errors)} 项，已跳过。"
        messagebox.showinfo("刷新完成", detail)

    def load_dynamic_options(self, key: object, post_after_load: bool = False) -> None:
        if not self._supports_dynamic_options(key):
            return
        env_key = getattr(key, "key", str(key))
        if env_key in self.dynamic_option_cache:
            self.filter_dynamic_options(key)
            widget = self.widgets.get(key)
            if post_after_load and isinstance(widget, ttk.Combobox):
                self._post_combobox(widget)
            return
        if env_key in self.dynamic_loading:
            if post_after_load and env_key in self.dynamic_loading:
                self.dynamic_post_after_load.add(env_key)
            self.filter_dynamic_options(key)
            return
        widget = self.widgets.get(key)
        if isinstance(widget, ttk.Combobox):
            widget.configure(values=("\u52a0\u8f7d\u4e2d...",))
        if post_after_load:
            self.dynamic_post_after_load.add(env_key)
        self.dynamic_loading.add(env_key)
        env_values = {getattr(item, "key", str(item)): parse_dynamic_display(var.get()) for item, var in self.vars.items()}
        env_values.update(read_env(self.env_path))

        def worker() -> None:
            try:
                values = tuple(item.display() for item in dynamic_env_options(env_key, env_values))
                error = ""
            except Exception as exc:
                values = ()
                error = str(exc)
            self.after(0, lambda: self._apply_single_dynamic_options(key, env_key, values, error))

        threading.Thread(target=worker, daemon=True).start()

    def _apply_single_dynamic_options(self, key: object, env_key: str, values: tuple[str, ...], error: str) -> None:
        self.dynamic_loading.discard(env_key)
        widget = self.widgets.get(key)
        if not isinstance(widget, ttk.Combobox):
            return
        if values:
            self.dynamic_option_cache[env_key] = values
            self.filter_dynamic_options(key)
            if env_key in self.dynamic_post_after_load:
                self.dynamic_post_after_load.discard(env_key)
                self._post_combobox(widget)
            return
        self.dynamic_post_after_load.discard(env_key)
        fallback = (f"\u52a0\u8f7d\u5931\u8d25: {error[:80]}",) if error else ()
        widget.configure(values=fallback)

    def _post_combobox(self, widget: ttk.Combobox) -> None:
        def post() -> None:
            try:
                widget.focus_set()
                try:
                    widget.tk.call("ttk::combobox::Unpost", str(widget))
                except tk.TclError:
                    pass
                widget.tk.call("ttk::combobox::Post", str(widget))
            except tk.TclError:
                try:
                    widget.event_generate("<Down>")
                except tk.TclError:
                    pass

        self.after(20, post)

    def filter_dynamic_options(self, key: object) -> None:
        env_key = getattr(key, "key", str(key))
        widget = self.widgets.get(key)
        if not isinstance(widget, ttk.Combobox):
            return
        options = self.dynamic_option_cache.get(env_key, ())
        if not options:
            return
        query = parse_dynamic_display(self.vars[key].get()).strip().lower()
        if not query:
            widget.configure(values=options)
            return
        filtered = tuple(item for item in options if query in item.lower())
        widget.configure(values=filtered or options)

    def _supports_dynamic_options(self, key: object) -> bool:
        env_key = getattr(key, "key", str(key))
        return env_key in {
            "HERO_SMS_SERVICE",
            "HERO_SMS_COUNTRY_SELECT",
            "GRIZZLY_SERVICE",
            "GRIZZLY_COUNTRY_SELECT",
            "FIVESIM_SERVICE",
            "FIVESIM_COUNTRY_SELECT",
            "SMSBOWER_SERVICE",
            "SMSBOWER_COUNTRY_SELECT",
            "SUB2API_GROUP_IDS",
        }


class PaypalAccountStatePage(ttk.Frame):
    STATUS_LABELS = {
        paypal_flow_state.STATUS_REGISTERED: _u(r"\u5df2\u6ce8\u518c"),
        paypal_flow_state.STATUS_LINK_READY: _u(r"\u5df2\u751f\u6210\u957f\u94fe\u63a5"),
        paypal_flow_state.STATUS_PAID_PENDING_AUTH: _u(r"\u5df2\u652f\u4ed8\u5f85\u6388\u6743"),
        paypal_flow_state.STATUS_COMPLETED: _u(r"\u5168\u6d41\u7a0b\u5b8c\u6210"),
        paypal_flow_state.STATUS_DISCARDED: _u(r"\u5df2\u5f03\u7f6e"),
    }

    def __init__(self, master: tk.Widget, root_path: Path):
        super().__init__(master)
        self.root_path = root_path
        self.state_file = root_path / "output" / "register_only" / "paypal_flow_state.json"
        self.discard_file = root_path / "output" / "register_only" / "paypal_flow_discarded_emails.txt"
        self.registered_file = root_path / "output" / "register_only" / "registered_sessions.txt"
        self.link_file = root_path / "output" / "\u0070\u0061\u0079\u0070\u0061\u006c\u6ce8\u518c" / "\u957f\u94fe\u63a5\u8d26\u53f7" / "account.txt"
        self.pending_file = root_path / "output" / "\u0070\u0061\u0079\u0070\u0061\u006c\u6ce8\u518c" / "\u5f85\u6388\u6743\u8d26\u53f7" / "account.txt"
        self._build()
        self.refresh()

    def _build(self) -> None:
        header = ttk.Frame(self)
        header.pack(fill=X, padx=10, pady=(10, 4))
        ttk.Label(header, text="PayPal账号状态", font=("Microsoft YaHei UI", 12, "bold")).pack(side=LEFT)
        ttk.Label(header, text=str(self.state_file), foreground="#555").pack(side=LEFT, padx=(14, 0))

        toolbar = ttk.Frame(self)
        toolbar.pack(fill=X, padx=10, pady=(0, 8))
        ttk.Button(toolbar, text="刷新", command=self.refresh).pack(side=LEFT, padx=2)
        ttk.Button(toolbar, text="标记弃置", command=self.mark_discarded).pack(side=LEFT, padx=2)
        ttk.Button(toolbar, text="恢复未完成", command=self.restore_unfinished).pack(side=LEFT, padx=2)
        ttk.Button(toolbar, text="标记完成", command=self.mark_completed).pack(side=LEFT, padx=2)
        ttk.Button(toolbar, text="查看弃置名单路径", command=self.show_discard_file).pack(side=LEFT, padx=2)

        self.summary_var = tk.StringVar(value="")
        ttk.Label(self, textvariable=self.summary_var).pack(fill=X, padx=10, pady=(0, 4))

        table_frame = ttk.Frame(self)
        table_frame.pack(fill=BOTH, expand=True, padx=10, pady=(0, 10))
        columns = ("email", "status", "stage", "updated_at", "error")
        self.table = ttk.Treeview(table_frame, columns=columns, show="headings", height=18, selectmode="extended")
        headings = {"email": "邮箱", "status": "状态", "stage": "失败阶段", "updated_at": "更新时间", "error": "最后错误"}
        widths = {"email": 260, "status": 140, "stage": 110, "updated_at": 170, "error": 420}
        for col in columns:
            self.table.heading(col, text=headings[col])
            self.table.column(col, width=widths[col], anchor="w")
        y_scroll = ttk.Scrollbar(table_frame, orient="vertical", command=self.table.yview)
        x_scroll = ttk.Scrollbar(table_frame, orient="horizontal", command=self.table.xview)
        self.table.configure(yscrollcommand=y_scroll.set, xscrollcommand=x_scroll.set)
        self.table.grid(row=0, column=0, sticky="nsew")
        y_scroll.grid(row=0, column=1, sticky="ns")
        x_scroll.grid(row=1, column=0, sticky="ew")
        table_frame.columnconfigure(0, weight=1)
        table_frame.rowconfigure(0, weight=1)

    def _use_runtime_state_files(self) -> None:
        paypal_flow_state.PAYPAL_FLOW_STATE_FILE = self.state_file
        paypal_flow_state.PAYPAL_FLOW_DISCARDED_FILE = self.discard_file

    def _sync_from_files(self) -> None:
        self._use_runtime_state_files()
        paypal_flow_state.sync_from_files(
            registered_file=self.registered_file,
            link_file=self.link_file,
            pending_file=self.pending_file,
        )

    def _load_state(self) -> dict[str, dict]:
        self._sync_from_files()
        state = paypal_flow_state.load_state(self.state_file)
        manual_discarded = paypal_flow_state.load_manual_discarded_emails(self.discard_file)
        for email in manual_discarded:
            record = dict(state.get(email) or {"email": email})
            record["status"] = paypal_flow_state.STATUS_DISCARDED
            state[email] = record
        return state

    def refresh(self) -> None:
        for item in self.table.get_children():
            self.table.delete(item)
        state = self._load_state()
        counts: dict[str, int] = {}
        for email, record in sorted(state.items()):
            status = str(record.get("status") or paypal_flow_state.STATUS_REGISTERED)
            counts[status] = counts.get(status, 0) + 1
            self.table.insert(
                "",
                END,
                iid=email,
                values=(
                    email,
                    self.STATUS_LABELS.get(status, status),
                    str(record.get("last_failed_stage") or record.get("stage") or ""),
                    str(record.get("updated_at") or ""),
                    str(record.get("last_error") or record.get("reason") or "")[:300],
                ),
            )
        parts = [f"{self.STATUS_LABELS.get(status, status)} {count}" for status, count in sorted(counts.items())]
        self.summary_var.set(" / ".join(parts) if parts else "暂无账号状态")

    def _selected_emails(self) -> list[str]:
        return [str(item) for item in self.table.selection()]

    def _require_selection(self) -> list[str]:
        emails = self._selected_emails()
        if not emails:
            messagebox.showwarning("未选择账号", "请先在表格中选择一个或多个账号")
        return emails

    def _append_discard_file(self, emails: list[str]) -> None:
        self.discard_file.parent.mkdir(parents=True, exist_ok=True)
        existing = paypal_flow_state.load_manual_discarded_emails(self.discard_file)
        stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        lines = []
        for email in emails:
            if email.lower() not in existing:
                lines.append(f"{email}\t{stamp}\tmanual_gui")
        if lines:
            with self.discard_file.open("a", encoding="utf-8") as fh:
                fh.write("\n".join(lines) + "\n")

    def _remove_from_discard_file(self, emails: list[str]) -> None:
        if not self.discard_file.exists():
            return
        targets = {email.lower() for email in emails}
        remaining = []
        for raw in self.discard_file.read_text(encoding="utf-8", errors="ignore").splitlines():
            match = EMAIL_RE.search(raw)
            if match and match.group(0).lower() in targets:
                continue
            remaining.append(raw)
        self.discard_file.write_text("\n".join(remaining) + ("\n" if remaining else ""), encoding="utf-8")

    def mark_discarded(self) -> None:
        emails = self._require_selection()
        if not emails:
            return
        if not messagebox.askyesno("确认弃置", f"确定弃置选中的 {len(emails)} 个账号吗？"):
            return
        self._use_runtime_state_files()
        self._append_discard_file(emails)
        paypal_flow_state.mark_discarded_many(emails, reason="manual_gui")
        self.refresh()

    def restore_unfinished(self) -> None:
        emails = self._require_selection()
        if not emails:
            return
        self._use_runtime_state_files()
        self._remove_from_discard_file(emails)
        state = paypal_flow_state.load_state(self.state_file)
        now = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
        pending_emails = paypal_flow_state.file_emails(self.pending_file)
        link_emails = paypal_flow_state.file_emails(self.link_file)
        for email in emails:
            key = email.lower()
            record = dict(state.get(key) or {"email": key})
            if key in pending_emails:
                status = paypal_flow_state.STATUS_PAID_PENDING_AUTH
            elif key in link_emails:
                status = paypal_flow_state.STATUS_LINK_READY
            else:
                status = paypal_flow_state.STATUS_REGISTERED
            record["status"] = status
            record["updated_at"] = now
            record.pop("reason", None)
            state[key] = record
        paypal_flow_state.save_state(state, self.state_file)
        self.refresh()

    def mark_completed(self) -> None:
        emails = self._require_selection()
        if not emails:
            return
        if not messagebox.askyesno("确认完成", f"确定把选中的 {len(emails)} 个账号标记为全流程完成吗？"):
            return
        self._use_runtime_state_files()
        self._remove_from_discard_file(emails)
        paypal_flow_state.mark_completed_many(emails)
        self.refresh()

    def show_discard_file(self) -> None:
        messagebox.showinfo("弃置名单", f"弃置名单路径:\n{self.discard_file}")


class RunPage(ttk.Frame):
    def __init__(self, master: tk.Widget, root_path: Path):
        super().__init__(master)
        self.root_path = root_path
        self.process: subprocess.Popen[str] | None = None
        self.queue: queue.Queue[str] = queue.Queue()
        self.success_count = 0
        self.failure_count = 0
        self._build()
        self.after(150, self._drain_queue)

    def _build(self) -> None:
        controls = ttk.LabelFrame(self, text="流程控制")
        controls.pack(fill=X, padx=10, pady=10)

        input_row = ttk.Frame(controls)
        input_row.pack(fill=X, padx=8, pady=(6, 3))
        button_row_1 = ttk.Frame(controls)
        button_row_1.pack(fill=X, padx=8, pady=(3, 2))
        button_row_2 = ttk.Frame(controls)
        button_row_2.pack(fill=X, padx=8, pady=(2, 2))
        button_row_3 = ttk.Frame(controls)
        button_row_3.pack(fill=X, padx=8, pady=(2, 6))

        ttk.Label(input_row, text="数量").pack(side=LEFT, padx=(0, 2))
        self.count_var = tk.StringVar(value="1")
        ttk.Entry(input_row, textvariable=self.count_var, width=8).pack(side=LEFT, padx=4)
        ttk.Label(input_row, text="并发").pack(side=LEFT, padx=(10, 2))
        self.workers_var = tk.StringVar(value="1")
        ttk.Entry(input_row, textvariable=self.workers_var, width=8).pack(side=LEFT, padx=4)
        ttk.Label(input_row, text="邮箱源").pack(side=LEFT, padx=(10, 2))
        self.mail_source_var = tk.StringVar(value="hotmail")
        self.mail_source_combo = ttk.Combobox(
            input_row,
            textvariable=self.mail_source_var,
            values=("default", "hotmail", "moemail", "icloud", "domain163"),
            width=10,
            state="readonly",
        )
        self.mail_source_combo.pack(side=LEFT, padx=4)
        ttk.Button(input_row, text="域名邮箱", command=lambda: self.mail_source_var.set("domain163")).pack(side=LEFT, padx=4)
        ttk.Label(input_row, text="指定邮箱").pack(side=LEFT, padx=(10, 2))
        self.email_var = tk.StringVar(value="")
        ttk.Entry(input_row, textvariable=self.email_var, width=36).pack(side=LEFT, padx=4)
        ttk.Button(button_row_1, text="\u4ec5\u6ce8\u518c\u8d26\u53f7", command=lambda: self.start("register-only")).pack(side=LEFT, padx=4)
        ttk.Button(button_row_1, text=_u(r"\u6d41\u7a0b1 \u751f\u6210\u7f8e\u533a\u957f\u94fe\u63a5"), command=lambda: self.start("paypal-flow1")).pack(side=LEFT, padx=4)
        ttk.Button(button_row_1, text=_u(r"\u6d41\u7a0b1 \u751f\u6210\u65e5\u533a\u957f\u94fe\u63a5"), command=lambda: self.start("paypal-flow1-jp")).pack(side=LEFT, padx=4)
        ttk.Button(button_row_1, text="流程2 真实卡PayPal", command=lambda: self.start("paypal-flow2")).pack(side=LEFT, padx=4)
        ttk.Button(button_row_1, text="流程2 无卡PayPal", command=lambda: self.start("paypal-flow2-nocard")).pack(side=LEFT, padx=4)
        ttk.Button(button_row_1, text="流程2 Filler脚本", command=lambda: self.start("paypal-flow2-filler")).pack(side=LEFT, padx=4)
        ttk.Button(button_row_1, text="流程3 授权落盘", command=lambda: self.start("paypal-flow3")).pack(side=LEFT, padx=4)
        ttk.Button(button_row_2, text="流程2 日本代理(真实卡)", command=lambda: self.start("paypal-flow2-jp")).pack(side=LEFT, padx=4)
        ttk.Button(button_row_2, text="流程2 日本代理(无卡)", command=lambda: self.start("paypal-flow2-jp-nocard")).pack(side=LEFT, padx=4)

        ttk.Button(button_row_3, text="全自动 真实卡", command=lambda: self.start("paypal-auto")).pack(side=LEFT, padx=4)
        ttk.Button(button_row_3, text="全自动 无卡", command=lambda: self.start("paypal-auto-nocard")).pack(side=LEFT, padx=4)
        ttk.Button(button_row_3, text="全自动 Filler脚本", command=lambda: self.start("paypal-auto-filler")).pack(side=LEFT, padx=4)
        ttk.Button(button_row_3, text="停止任务", command=self.stop).pack(side=RIGHT, padx=8)

        self.summary_var = tk.StringVar(value="成功 0 / 失败 0")
        ttk.Label(self, textvariable=self.summary_var).pack(fill=X, padx=10)

        result_frame = ttk.LabelFrame(self, text="结果输出")
        result_frame.pack(fill=BOTH, expand=True, padx=10, pady=(6, 4))
        columns = ("time", "flow", "account", "status", "message", "path")
        self.results = ttk.Treeview(result_frame, columns=columns, show="headings", height=7)
        headings = {"time": "时间", "flow": "流程", "account": "账号/邮箱", "status": "状态", "message": "结果", "path": "输出路径"}
        widths = {"time": 110, "flow": 110, "account": 210, "status": 70, "message": 360, "path": 280}
        for col in columns:
            self.results.heading(col, text=headings[col])
            self.results.column(col, width=widths[col], anchor="w")
        self.results.pack(fill=BOTH, expand=True, padx=4, pady=4)
        ttk.Button(result_frame, text="清空结果", command=self.clear_results).pack(anchor="e", padx=4, pady=(0, 4))

        log_frame = ttk.LabelFrame(self, text="实时日志")
        log_frame.pack(fill=BOTH, expand=True, padx=10, pady=(4, 10))
        self.log_text = tk.Text(log_frame, height=12, wrap="word")
        self.log_text.pack(fill=BOTH, expand=True, padx=4, pady=4)

    def _runner_command(self, action: str) -> list[str]:
        count = self.count_var.get().strip() or "1"
        workers = self.workers_var.get().strip() or "1"
        base_args = ["--runner", action, "--count", count, "--workers", workers]
        mail_source = self.mail_source_var.get().strip()
        if mail_source and mail_source != "default":
            base_args.extend(["--mail-source", mail_source])
        selected_email = self.email_var.get().strip()
        if selected_email:
            base_args.extend(["--email", selected_email])
        if getattr(sys, "frozen", False):
            return [sys.executable, *base_args]
        return [sys.executable, str(Path(__file__).resolve()), *base_args]

    def start(self, action: str) -> None:
        if self.process and self.process.poll() is None:
            messagebox.showwarning("任务运行中", "请先停止当前任务")
            return
        cmd = self._runner_command(action)
        self._append_log(f"> {' '.join(cmd)}\n")
        self.process = subprocess.Popen(
            cmd,
            cwd=str(self.root_path),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
            env={
                **os.environ,
                "CODEX_COLOR_LOGS": "0",
                "NO_COLOR": "1",
                "PYTHONIOENCODING": "utf-8",
            },
        )
        threading.Thread(target=self._reader_thread, daemon=True).start()

    def stop(self) -> None:
        if self.process and self.process.poll() is None:
            self.process.terminate()
            self._append_log("[panel] 已请求停止任务\n")

    def clear_results(self) -> None:
        for item in self.results.get_children():
            self.results.delete(item)
        self.success_count = 0
        self.failure_count = 0
        self._update_summary()

    def _reader_thread(self) -> None:
        assert self.process and self.process.stdout
        for line in self.process.stdout:
            self.queue.put(line)
        code = self.process.wait()
        self.queue.put(f"[panel] 子进程退出，code={code}\n")

    def _drain_queue(self) -> None:
        while True:
            try:
                line = self.queue.get_nowait()
            except queue.Empty:
                break
            clean_line = strip_ansi_for_display(line)
            self._append_log(clean_line)
            self._parse_result_line(clean_line)
        self.after(150, self._drain_queue)

    def _append_log(self, text: str) -> None:
        self.log_text.insert(END, text)
        self.log_text.see(END)

    def _parse_result_line(self, line: str) -> None:
        stripped = line.strip()
        if not stripped:
            return
        try:
            payload = json.loads(stripped)
        except json.JSONDecodeError:
            payload = None
        if isinstance(payload, dict) and payload.get("type") == "result":
            self._add_result(
                flow=str(payload.get("flow") or ""),
                account=str(payload.get("account") or ""),
                status=str(payload.get("status") or ""),
                message=str(payload.get("message") or ""),
                path=str(payload.get("path") or ""),
            )
            return

        failure_markers = ("[FAIL]", "失败", "failed:", "RuntimeError", "Traceback")
        success_markers = ("[OK]", "成功", "link ok", "支付成功", "授权成功")
        if any(marker in stripped for marker in failure_markers):
            self._add_result("", self._extract_email(stripped), "failure", stripped[:360], "")
        elif any(marker in stripped for marker in success_markers):
            self._add_result("", self._extract_email(stripped), "success", stripped[:360], "")

    def _extract_email(self, text: str) -> str:
        match = EMAIL_RE.search(text)
        return match.group(0) if match else ""

    def _add_result(self, flow: str, account: str, status: str, message: str, path: str) -> None:
        normalized = status.lower()
        if normalized == "success":
            self.success_count += 1
        elif normalized == "failure":
            self.failure_count += 1
        now = datetime.now().strftime("%H:%M:%S")
        self.results.insert("", END, values=(now, flow, account, status, message, path))
        self._update_summary()

    def _update_summary(self) -> None:
        self.summary_var.set(f"成功 {self.success_count} / 失败 {self.failure_count}")


class ControlPanelApp(tk.Tk):
    def __init__(self, root_path: Path):
        super().__init__()
        self.root_path = root_path
        self.title("ChatGPT Assistant 控制面板 - 作者：hanyiz2")
        self.geometry("1220x780")
        self.minsize(1040, 680)
        self.files = get_panel_files(root_path)
        self.pages: dict[str, ttk.Frame] = {}
        self._configure_style()
        self._build()

    def _configure_style(self) -> None:
        style = ttk.Style(self)
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass
        style.configure("Nav.TButton", anchor="w", padding=(10, 7))

    def _build(self) -> None:
        main = ttk.Frame(self)
        main.pack(fill=BOTH, expand=True)
        nav = ttk.Frame(main, width=220)
        nav.pack(side=LEFT, fill=Y, padx=(8, 4), pady=8)
        content = ttk.Frame(main)
        content.pack(side=RIGHT, fill=BOTH, expand=True, padx=(4, 8), pady=8)
        self.content = content

        ttk.Label(nav, text="ChatGPT Assistant", font=("Microsoft YaHei UI", 12, "bold")).pack(fill=X, padx=8, pady=(4, 8))
        ttk.Label(nav, text="作者：hanyiz2", foreground="#666").pack(fill=X, padx=8, pady=(0, 10))
        self._add_nav(nav, "运行流程", lambda: self.show_page("run"))
        self._add_nav(nav, "API Key / 常用配置", lambda: self.show_page("env"))
        self._add_nav(nav, "PayPal账号状态", lambda: self.show_page("paypal_state"))
        resource_groups = [
            ("代理池", ["proxy_default", "proxy_jp", "proxy_us", "proxy_de"]),
            ("卡密池", ["paypal_card_codes", "paypal_card_codes_used", "paypal_card_codes_failed"]),
            ("虚拟卡池", ["paypal_cards"]),
            ("手机号池", ["paypal_phones", "auth_phones"]),
            ("邮箱池", ["hotmail_accounts", "hotmail_mail_pool", "icloud_accounts", "icloud_mail_pool", "mail_accounts", "mail_pool"]),
            ("\u4ec5\u6ce8\u518c\u8f93\u51fa", ["register_only_sessions", "register_only_used", "paypal_flow_state", "paypal_flow_discarded"]),
            ("长链接池", ["paypal_links"]),
            ("授权账号/输出", ["paypal_pending_auth", "paypal_authorized_rt", "paypal_authorized_sub"]),
        ]
        for title, keys in resource_groups:
            self._add_nav(nav, title, lambda keys=keys, title=title: self.show_resource_group(title, keys))

        self.pages["run"] = RunPage(content, self.root_path)
        self.pages["env"] = EnvPage(content, self.root_path / ".env")
        self.pages["paypal_state"] = PaypalAccountStatePage(content, self.root_path)
        self.show_page("run")

    def _add_nav(self, nav: ttk.Frame, text: str, command) -> None:
        ttk.Button(nav, text=text, command=command, style="Nav.TButton").pack(fill=X, padx=6, pady=3)

    def _clear_content(self) -> None:
        for child in self.content.winfo_children():
            child.pack_forget()

    def show_page(self, key: str) -> None:
        self._clear_content()
        self.pages[key].pack(fill=BOTH, expand=True)

    def show_resource_group(self, title: str, keys: list[str]) -> None:
        page_key = "group:" + ",".join(keys)
        if page_key not in self.pages:
            frame = ttk.Frame(self.content)
            tabs = ttk.Notebook(frame)
            tabs.pack(fill=BOTH, expand=True)
            for key in keys:
                panel_file = self.files[key]
                tabs.add(ResourcePage(tabs, panel_file), text=panel_file.label)
            self.pages[page_key] = frame
        self._clear_content()
        self.pages[page_key].pack(fill=BOTH, expand=True)


def main() -> int:
    root_path = app_root()
    app = ControlPanelApp(root_path)
    app.mainloop()
    return 0


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--runner":
        raise SystemExit(panel_runner.main(sys.argv[2:]))
    raise SystemExit(main())
