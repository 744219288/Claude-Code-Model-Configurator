"""Beginner-friendly Chinese Tkinter GUI."""

from __future__ import annotations

import queue
import re
import sys
import threading
import tkinter as tk
from datetime import datetime
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from .core import (
    APP_VERSION, DEFAULT_MODEL, InstallOptions, InstallSourceError, Paths, check_updates,
    detect_system_proxy, export_diagnostics, install_all, launch_claude, launch_vscode,
    legacy_external_cleanup_candidates, system_status, test_connection, uninstall_all,
    prepare_configurator_update, schedule_configurator_update, update_managed_claude,
)
from .credentials import CredentialError, read_api_key, write_api_key


BG = "#f5f6fa"
CARD = "#ffffff"
PRIMARY = "#5b55e7"
PRIMARY_DARK = "#4942cf"
PRIMARY_SOFT = "#eeedff"
SOFT = "#f7f7fc"
LINE = "#e4e6ef"
TEXT = "#202230"
MUTED = "#6f7382"
SUCCESS = "#117a56"
ERROR = "#bd3742"
SIDEBAR = "#4338ca"
SIDEBAR_TEXT = "#e9e8ff"


def resource_path(relative: str) -> Path:
    base = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent.parent))
    return base / relative


def enable_high_dpi() -> None:
    if sys.platform != "win32":
        return
    try:
        import ctypes

        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        pass

INSTALL_TASKS = (
    ("系统检查", "系统检查"),
    ("安装包完整性", "验证并固定完整安装资源"),
    ("离线组件", "检查 Git 与 Node.js 离线包"),
    ("安全保存", "安全保存 API Key"),
    ("直连网关", "配置 DeepSeek Anthropic 直连"),
    ("Git Bash", "准备隔离的 Git for Windows"),
    ("安装源检测", "检测国内与官方安装源"),
    ("npm 运行环境", "准备受管理 Node.js/npm"),
    ("Claude Code", "安装 Claude Code"),
    ("VS Code", "安装 VS Code"),
    ("VS Code 扩展", "安装 Claude Code 扩展"),
    ("配置", "生成并保存配置"),
    ("终端入口", "配置终端 claude 命令"),
    ("连接测试", "测试 DeepSeek 连接"),
    ("桌面入口", "创建 Claude Code + DeepSeek 快捷方式"),
)


class App(tk.Tk):
    def __init__(self) -> None:
        enable_high_dpi()
        super().__init__()
        self.paths = Paths.default()
        self.events: queue.Queue[tuple[str, object]] = queue.Queue()
        self.title(f"Claude Code + DeepSeek 一键配置器 V{APP_VERSION} 安全增强版")
        self.geometry("960x760")
        self.minsize(900, 720)
        self.configure(bg=BG)
        self.model = tk.StringVar(value=DEFAULT_MODEL)
        self.install_vscode = tk.BooleanVar(value=False)
        self.use_detected_proxy = tk.BooleanVar(value=False)
        self.detected_proxies = detect_system_proxy()
        self.key = tk.StringVar()
        self.status = tk.StringVar(value="等待开始")
        self.detail = tk.StringVar(value="只需输入 API Key，其余步骤会自动完成。")
        self.progress_value = tk.DoubleVar(value=0)
        self.progress_animating = False
        self.completed_tasks: set[str] = set()
        self.active_task: str | None = None
        self.hide_key_after_id: str | None = None
        self.model_buttons: dict[str, tk.Button] = {}
        self.app_icon_full: tk.PhotoImage | None = None
        self.app_icon_large: tk.PhotoImage | None = None
        self._build()
        self.after(20, self._apply_window_style)
        self.after(30, self._center_window)
        self.after(120, self._poll)
        try:
            if read_api_key():
                self.detail.set("已在 Windows 凭据管理器中找到保存的 API Key，可直接测试连接。")
        except CredentialError:
            pass

    def _load_brand_icon(self) -> None:
        try:
            self.app_icon_full = tk.PhotoImage(file=str(resource_path("assets/app_icon.png")))
            factor = max(1, self.app_icon_full.width() // 88)
            self.app_icon_large = self.app_icon_full.subsample(factor, factor)
            self.iconphoto(True, self.app_icon_full)
            icon_file = resource_path("assets/app_icon.ico")
            if icon_file.is_file():
                self.iconbitmap(default=str(icon_file))
        except (tk.TclError, OSError):
            self.app_icon_full = None
            self.app_icon_large = None

    def _center_window(self) -> None:
        self.update_idletasks()
        width = self.winfo_width()
        height = self.winfo_height()
        x = max(0, (self.winfo_screenwidth() - width) // 2)
        y = max(0, (self.winfo_screenheight() - height) // 2)
        self.geometry(f"{width}x{height}+{x}+{y}")

    def _apply_window_style(self) -> None:
        if sys.platform != "win32":
            return
        try:
            import ctypes

            preference = ctypes.c_int(2)
            ctypes.windll.dwmapi.DwmSetWindowAttribute(
                self.winfo_id(), 33, ctypes.byref(preference), ctypes.sizeof(preference),
            )
        except Exception:
            pass

    @staticmethod
    def _section_label(parent: tk.Widget, text: str) -> tk.Label:
        return tk.Label(
            parent, text=text, bg=CARD, fg=TEXT, anchor="w",
            font=("Microsoft YaHei UI", 10, "bold"),
        )

    @staticmethod
    def _bind_hover(button: tk.Button, normal: str, hover: str) -> None:
        button.bind("<Enter>", lambda _event: button.config(bg=hover) if button.cget("state") != "disabled" else None)
        button.bind("<Leave>", lambda _event: button.config(bg=normal) if button.cget("state") != "disabled" else None)

    def _build(self) -> None:
        self._load_brand_icon()
        style = ttk.Style(self)
        style.theme_use("clam")
        style.configure("Overall.Horizontal.TProgressbar", troughcolor="#e9eaf2", background=PRIMARY, bordercolor="#e9eaf2", lightcolor=PRIMARY, darkcolor=PRIMARY, thickness=8)
        style.configure("Current.Horizontal.TProgressbar", troughcolor="#e5e6f0", background="#746ff0", bordercolor="#e5e6f0", lightcolor="#746ff0", darkcolor="#746ff0", thickness=7)

        shell = tk.Frame(self, bg=BG)
        shell.pack(fill="both", expand=True)

        sidebar = tk.Frame(shell, bg=SIDEBAR, width=274, padx=28, pady=24)
        sidebar.pack(side="left", fill="y")
        sidebar.pack_propagate(False)
        if self.app_icon_large:
            tk.Label(sidebar, image=self.app_icon_large, bg=SIDEBAR, bd=0).pack(anchor="w", pady=(0, 20))
        else:
            tk.Label(sidebar, text=">", width=4, height=2, bg="#5b55e7", fg="white", font=("Consolas", 22, "bold")).pack(anchor="w", pady=(0, 20))
        tk.Label(sidebar, text="Claude Code\n+ DeepSeek", bg=SIDEBAR, fg="white", justify="left", anchor="w", font=("Microsoft YaHei UI", 20, "bold")).pack(fill="x")
        tk.Label(sidebar, text="一次配置，自动准备 Claude Code 与 DeepSeek 直连环境。", bg=SIDEBAR, fg=SIDEBAR_TEXT, justify="left", anchor="w", wraplength=210, font=("Microsoft YaHei UI", 10)).pack(fill="x", pady=(10, 22))
        tk.Frame(sidebar, bg="#665ee0", height=1).pack(fill="x", pady=(0, 20))
        for title, subtitle in (
            ("✓  凭据隔离", "API Key 保存在 Windows 凭据管理器"),
            ("✓  密钥隔离", "仅注入到启动的 Claude 子进程"),
            ("✓  依赖校验", "Git 与 Node.js 全量 SHA-256 锁定"),
        ):
            tk.Label(sidebar, text=title, bg=SIDEBAR, fg="white", anchor="w", font=("Microsoft YaHei UI", 10, "bold")).pack(fill="x")
            tk.Label(sidebar, text=subtitle, bg=SIDEBAR, fg="#c9c7f7", anchor="w", wraplength=210, font=("Microsoft YaHei UI", 8)).pack(fill="x", pady=(3, 14))
        tk.Label(sidebar, text=f"V{APP_VERSION}  安全增强版", bg="#5a50d3", fg="#f4f3ff", padx=10, pady=5, font=("Segoe UI", 8, "bold")).pack(side="bottom", anchor="w")

        content = tk.Frame(shell, bg=BG, padx=30, pady=16)
        content.pack(side="left", fill="both", expand=True)
        heading = tk.Frame(content, bg=BG)
        heading.pack(fill="x", pady=(0, 10))
        tk.Label(heading, text="开始配置", bg=BG, fg=TEXT, anchor="w", font=("Microsoft YaHei UI", 20, "bold")).pack(anchor="w")
        tk.Label(heading, text="粘贴 API Key，剩余步骤交给配置器。", bg=BG, fg=MUTED, anchor="w", font=("Microsoft YaHei UI", 9)).pack(anchor="w", pady=(3, 0))

        card = tk.Frame(content, bg=CARD, padx=20, pady=14, highlightbackground=LINE, highlightthickness=1)
        card.pack(fill="x")
        label_row = tk.Frame(card, bg=CARD)
        label_row.pack(fill="x")
        self._section_label(label_row, "DeepSeek API Key").pack(side="left")
        tk.Label(label_row, text="仅保存在本机凭据管理器", bg=CARD, fg=SUCCESS, font=("Microsoft YaHei UI", 8)).pack(side="right")
        key_row = tk.Frame(card, bg=CARD)
        key_row.pack(fill="x", pady=(7, 13))
        entry_border = tk.Frame(key_row, bg=LINE, padx=1, pady=1)
        entry_border.pack(side="left", fill="x", expand=True)
        self.key_entry = tk.Entry(entry_border, textvariable=self.key, show="●", relief="flat", bd=0, bg="#fbfbfd", fg=TEXT, insertbackground=TEXT, font=("Segoe UI", 11))
        self.key_entry.pack(fill="x", ipady=9, padx=11)
        self.show_key = tk.BooleanVar(value=False)
        tk.Checkbutton(key_row, text="临时显示", variable=self.show_key, command=self._toggle_key_visibility, bg=CARD, fg=MUTED, selectcolor=CARD, activebackground=CARD, activeforeground=TEXT, font=("Microsoft YaHei UI", 8)).pack(side="left", padx=(10, 0))

        self._section_label(card, "选择模型").pack(fill="x")
        model_row = tk.Frame(card, bg=CARD)
        model_row.pack(fill="x", pady=(7, 13))
        for index, (value, label) in enumerate((
            ("deepseek-v4-flash", "DeepSeek V4 Flash\n快速 · 日常推荐"),
            ("deepseek-v4-pro[1m]", "DeepSeek V4 Pro 1M\n推理 · 复杂任务"),
        )):
            button = tk.Button(
                model_row, text=label, command=lambda selected=value: self._select_model(selected),
                justify="left", anchor="w", relief="flat", bd=0, padx=13, pady=8,
                cursor="hand2", font=("Microsoft YaHei UI", 8),
            )
            button.pack(side="left", fill="x", expand=True, padx=(0, 5) if index == 0 else (5, 0))
            self.model_buttons[value] = button
        self._refresh_model_buttons()

        option_row = tk.Frame(card, bg=CARD)
        option_row.pack(fill="x", pady=(0, 11))
        tk.Checkbutton(
            option_row, text="同时安装 VS Code 与 Claude 扩展（可选）",
            variable=self.install_vscode, bg=CARD, fg=MUTED, selectcolor=CARD,
            activebackground=CARD, activeforeground=TEXT,
            font=("Microsoft YaHei UI", 8),
        ).pack(side="left")
        detected_proxy = self.detected_proxies.get("https") or self.detected_proxies.get("http")
        if detected_proxy:
            tk.Checkbutton(
                option_row, text=f"使用检测到的系统代理 {detected_proxy}",
                variable=self.use_detected_proxy, bg=CARD, fg=MUTED, selectcolor=CARD,
                activebackground=CARD, activeforeground=TEXT,
                font=("Microsoft YaHei UI", 8),
            ).pack(side="right")

        self.install_button = tk.Button(card, text="开始安装并配置   →", command=self._install, bg=PRIMARY, fg="white", activebackground=PRIMARY_DARK, activeforeground="white", disabledforeground="#d9d7ff", relief="flat", bd=0, cursor="hand2", font=("Microsoft YaHei UI", 10, "bold"), pady=10)
        self.install_button.pack(fill="x")
        self._bind_hover(self.install_button, PRIMARY, PRIMARY_DARK)

        progress_row = tk.Frame(content, bg=BG)
        progress_row.pack(fill="x", pady=(14, 0))
        tk.Label(progress_row, text="总体进度", bg=CARD, fg=MUTED, font=("Microsoft YaHei UI", 9)).pack(side="left")
        progress_row.configure(bg=BG)
        for child in progress_row.winfo_children():
            child.configure(bg=BG)
        self.progress_text = tk.Label(progress_row, text="等待开始", bg=BG, fg=PRIMARY, font=("Segoe UI", 9, "bold"))
        self.progress_text.pack(side="right")
        self.progress_bar = ttk.Progressbar(content, variable=self.progress_value, maximum=100, mode="determinate", style="Overall.Horizontal.TProgressbar")
        self.progress_bar.pack(fill="x", pady=(5, 12))

        self.task_panel = tk.Frame(content, bg=SOFT, padx=15, pady=10, highlightbackground=LINE, highlightthickness=1)
        self.task_panel.pack(fill="x", pady=(0, 8))
        task_top = tk.Frame(self.task_panel, bg=SOFT)
        task_top.pack(fill="x")
        self.task_badge = tk.Label(task_top, text="…", bg=PRIMARY, fg="white", width=3, height=1, font=("Segoe UI", 10, "bold"))
        self.task_badge.pack(side="left", padx=(0, 10))
        task_names = tk.Frame(task_top, bg=SOFT)
        task_names.pack(side="left", fill="x", expand=True)
        self.task_title = tk.Label(task_names, text="等待开始", bg=SOFT, fg=TEXT, anchor="w", font=("Microsoft YaHei UI", 11, "bold"))
        self.task_title.pack(fill="x")
        self.task_meta = tk.Label(task_names, text="输入 API Key 后点击上方按钮", bg=SOFT, fg=MUTED, anchor="w", font=("Microsoft YaHei UI", 8))
        self.task_meta.pack(fill="x", pady=(2, 0))
        self.task_state = tk.Label(task_top, text="准备就绪", bg=SOFT, fg=PRIMARY, font=("Microsoft YaHei UI", 9, "bold"))
        self.task_state.pack(side="right", padx=(10, 0))
        self.current_task_value = tk.DoubleVar(value=0)
        self.current_task_bar = ttk.Progressbar(self.task_panel, variable=self.current_task_value, maximum=100, mode="determinate", style="Current.Horizontal.TProgressbar")
        self.current_task_bar.pack(fill="x", pady=(11, 7))
        self.task_detail = tk.Label(self.task_panel, text="安装到哪一步，就在这里显示哪一步。", bg=SOFT, fg=MUTED, anchor="w", justify="left", wraplength=560, font=("Microsoft YaHei UI", 8))
        self.task_detail.pack(fill="x")

        action_row = tk.Frame(content, bg=BG)
        action_row.pack(fill="x", pady=(0, 6))
        self.test_button = self._action_button(action_row, "测试连接", self._test)
        self.test_button.pack(side="left", fill="x", expand=True, padx=(0, 3))
        self.launch_claude_button = self._action_button(action_row, "启动 Claude Code", lambda: self._launch(launch_claude))
        self.launch_claude_button.pack(side="left", fill="x", expand=True, padx=3)
        self.launch_vscode_button = self._action_button(action_row, "启动 VS Code", lambda: self._launch(launch_vscode))
        self.launch_vscode_button.pack(side="left", fill="x", expand=True, padx=3)
        self.update_button = self._action_button(action_row, "检查更新", self._check_updates, quiet=True)
        self.update_button.pack(side="left", fill="x", expand=True, padx=3)
        self.status_button = self._action_button(action_row, "状态", self._show_status, quiet=True)
        self.status_button.pack(side="left", fill="x", expand=True, padx=3)
        self.diagnostics_button = self._action_button(action_row, "诊断", self._export_diagnostics, quiet=True)
        self.diagnostics_button.pack(side="left", fill="x", expand=True, padx=3)
        self.uninstall_button = self._action_button(action_row, "卸载", self._uninstall, quiet=True)
        self.uninstall_button.pack(side="left", fill="x", expand=True, padx=(3, 0))

        status_box = tk.Frame(content, bg=CARD, padx=14, pady=8, highlightbackground=LINE, highlightthickness=1)
        status_box.pack(fill="both", expand=True)
        tk.Label(status_box, textvariable=self.status, bg=CARD, fg=PRIMARY, anchor="w", font=("Microsoft YaHei UI", 10, "bold")).pack(fill="x")
        tk.Label(status_box, textvariable=self.detail, bg=CARD, fg=MUTED, anchor="nw", justify="left", wraplength=560, font=("Microsoft YaHei UI", 8)).pack(fill="x", pady=(3, 0))
        self.log = tk.Text(status_box, height=2, bg=SOFT, fg="#555a69", relief="flat", wrap="word", font=("Microsoft YaHei UI", 8), padx=8, pady=5, state="disabled")
        self.log.pack(fill="both", expand=True, pady=(7, 0))

    def _select_model(self, value: str) -> None:
        self.model.set(value)
        self._refresh_model_buttons()

    def _refresh_model_buttons(self) -> None:
        selected = self.model.get()
        for value, button in self.model_buttons.items():
            active = value == selected
            button.config(
                bg=PRIMARY_SOFT if active else SOFT,
                fg=PRIMARY_DARK if active else MUTED,
                activebackground=PRIMARY_SOFT if active else "#eeeeF5",
                activeforeground=PRIMARY_DARK if active else TEXT,
                highlightbackground=PRIMARY if active else LINE,
                highlightthickness=1,
            )

    def _action_button(self, parent: tk.Widget, text: str, command, quiet: bool = False) -> tk.Button:
        normal = CARD if quiet else PRIMARY_SOFT
        hover = "#f0f1f6" if quiet else "#dfddff"
        button = tk.Button(
            parent, text=text, command=command, bg=normal, fg=MUTED if quiet else PRIMARY,
            activebackground=hover, activeforeground=TEXT if quiet else PRIMARY_DARK,
            highlightbackground=LINE, highlightthickness=1, relief="flat", bd=0,
            cursor="hand2", font=("Microsoft YaHei UI", 8, "bold"), pady=7,
        )
        self._bind_hover(button, normal, hover)
        return button

    def _hide_api_key(self) -> None:
        self.show_key.set(False)
        self.key_entry.config(show="●")
        self.hide_key_after_id = None

    def _toggle_key_visibility(self) -> None:
        if self.hide_key_after_id:
            self.after_cancel(self.hide_key_after_id)
            self.hide_key_after_id = None
        if self.show_key.get():
            self.key_entry.config(show="")
            self.hide_key_after_id = self.after(8000, self._hide_api_key)
        else:
            self._hide_api_key()

    def _set_busy(self, busy: bool) -> None:
        self.install_button.config(state="disabled" if busy else "normal", text="正在安装，请稍候…" if busy else "开始安装并配置   →")
        action_state = "disabled" if busy else "normal"
        for button in (
            self.test_button, self.launch_claude_button, self.launch_vscode_button,
            self.update_button, self.status_button, self.diagnostics_button, self.uninstall_button,
        ):
            button.config(state=action_state)
        if busy:
            self._set_indeterminate("处理中")

    def _stop_progress_animation(self) -> None:
        if self.progress_animating:
            self.progress_bar.stop()
            self.progress_animating = False

    def _reset_tasks(self) -> None:
        self.active_task = None
        self.completed_tasks.clear()
        self.current_task_bar.stop()
        self.current_task_bar.config(mode="determinate")
        self.current_task_value.set(0)
        self.task_badge.config(text="1")
        self.task_title.config(text="准备开始")
        self.task_meta.config(text=f"共 {len(INSTALL_TASKS)} 个步骤")
        self.task_state.config(text="准备中", fg=PRIMARY)
        self.task_detail.config(text="正在准备自动安装流程…")

    def _complete_task(self, key: str) -> None:
        if key in dict(INSTALL_TASKS):
            self.completed_tasks.add(key)

    def _update_task_progress(self, status: str, detail: str) -> None:
        key = status
        if status == "下载运行环境":
            key = self.active_task or "npm 运行环境"
        if key == "完成":
            if self.active_task:
                self._complete_task(self.active_task)
            self.completed_tasks.update(task_key for task_key, _ in INSTALL_TASKS)
            self.current_task_bar.stop()
            self.current_task_bar.config(mode="determinate")
            self.current_task_value.set(100)
            self.task_badge.config(text="✓", bg=SUCCESS)
            self.task_title.config(text="全部配置完成")
            self.task_meta.config(text=f"已完成 {len(INSTALL_TASKS)} 个步骤")
            self.task_state.config(text="100%", fg=SUCCESS)
            self.task_detail.config(text=detail)
            self._stop_progress_animation()
            self.progress_bar.config(mode="determinate")
            self.progress_value.set(100)
            self.progress_text.config(text="100%")
            return
        task_map = dict(INSTALL_TASKS)
        if key not in task_map:
            return
        if self.active_task and self.active_task != key:
            self._complete_task(self.active_task)
        self.active_task = key
        task_index = next(i for i, item in enumerate(INSTALL_TASKS) if item[0] == key)
        self.current_task_bar.stop()
        self.task_badge.config(text=str(task_index + 1), bg=PRIMARY)
        self.task_title.config(text=task_map[key])
        self.task_meta.config(text=f"第 {task_index + 1} / {len(INSTALL_TASKS)} 步 · 已完成 {len(self.completed_tasks)} 步")
        self.task_detail.config(text=detail.replace("\n", " · "))
        match = re.search(r"(?<!\d)(100|[1-9]?\d)%", detail)
        if match:
            percent = int(match.group(1))
            self.current_task_bar.config(mode="determinate")
            self.current_task_value.set(percent)
            self.task_state.config(text=f"{percent}%", fg=PRIMARY)
        else:
            self.current_task_bar.config(mode="indeterminate")
            self.current_task_bar.start(12)
            self.task_state.config(text="进行中", fg=PRIMARY)
        fraction = int(match.group(1)) / 100 if match else 0.25
        overall = min(99, int((task_index + fraction) * 100 / len(INSTALL_TASKS)))
        self._stop_progress_animation()
        self.progress_bar.config(mode="determinate")
        self.progress_value.set(overall)
        self.progress_text.config(text=f"{overall}%")

    def _set_indeterminate(self, text: str) -> None:
        if not self.progress_animating:
            self.progress_bar.config(mode="indeterminate")
            self.progress_bar.start(12)
            self.progress_animating = True
        self.progress_text.config(text=text)

    def _update_progress_bar(self, status: str, detail: str) -> None:
        match = re.search(r"(?<!\d)(100|[1-9]?\d)%", detail)
        if match:
            self._stop_progress_animation()
            percent = int(match.group(1))
            self.progress_bar.config(mode="determinate")
            self.progress_value.set(percent)
            self.progress_text.config(text=f"{percent}%")
        else:
            self._set_indeterminate("进行中")

    def _worker(self, target) -> None:
        def wrapped():
            try:
                target()
            except InstallSourceError as exc:
                self.events.put(("install-options", exc))
            except Exception as exc:
                self.events.put(("error", str(exc)))
            finally:
                self.events.put(("busy", "0"))
        self._set_busy(True)
        threading.Thread(target=wrapped, daemon=True).start()

    def _progress(self, step: str, detail: str) -> None:
        self.events.put(("progress", f"{step}\n{detail}"))

    def _install(self, strategy: str = "auto") -> None:
        api_key = self.key.get().strip()
        if not api_key:
            messagebox.showwarning("需要 API Key", "请先粘贴 DeepSeek API Key。")
            self.key_entry.focus_set()
            return
        model = self.model.get()
        self._hide_api_key()
        self._reset_tasks()
        self.progress_value.set(0)
        self.progress_text.config(text="0%")
        proxy = ""
        if self.use_detected_proxy.get():
            proxy = self.detected_proxies.get("https") or self.detected_proxies.get("http") or ""
        options = InstallOptions(
            claude_strategy=strategy,
            install_vscode=bool(self.install_vscode.get()),
            proxy_url=proxy,
        )
        self._worker(lambda: install_all(self.paths, api_key, model, self._progress, options))

    def _test(self) -> None:
        api_key = self.key.get().strip()
        self._hide_api_key()
        if api_key:
            try:
                write_api_key(api_key)
            except Exception as exc:
                messagebox.showerror("无法保存", str(exc))
                return
        def work():
            self.events.put(("progress", "连接测试\n正在连接 DeepSeek…"))
            ok, message = test_connection(self.model.get())
            self.events.put(("success" if ok else "error", message))
        self._worker(work)

    def _launch(self, launcher) -> None:
        def work():
            self.events.put(("progress", "启动中\n正在注入 DeepSeek 直连环境…"))
            launcher(self.paths)
            self.events.put(("success", "已启动。请在新窗口中开始使用。"))
        self._worker(work)

    def _show_status(self) -> None:
        def work():
            status = system_status(self.paths)
            message = (
                f"连接：DeepSeek Anthropic 直连\n"
                f"接口：{status['gateway_url']}\n"
                f"API Key：{'已安全保存' if status['api_key_saved'] else '未保存'}\n"
                f"当前模型：{status['model']}\n"
                f"Git Bash：{'已就绪' if status['git_ready'] else '未安装'}\n"
                f"Claude Code：{'已找到' if status['claude_ready'] else '未找到'}\n"
                f"安装资源：{'完整' if status['payload_ready'] else status['payload_detail']}\n"
                f"VS Code：{'已找到' if status['vscode_ready'] else '未找到（可选）'}"
            )
            self.events.put(("info", message))
        self._worker(work)

    def _check_updates(self) -> None:
        def work():
            self.events.put(("progress", "检查更新\n正在检查配置器和受管理组件版本…"))
            self.events.put(("updates", check_updates(self.paths)))
        self._worker(work)

    def _update_managed_claude(self, dialog: tk.Toplevel | None = None) -> None:
        if dialog is not None:
            dialog.destroy()
        def work():
            message = update_managed_claude(self.paths, self._progress)
            self.events.put(("success", message))
        self._worker(work)

    def _update_configurator(self, manifest: dict[str, object], dialog: tk.Toplevel) -> None:
        dialog.destroy()
        def work():
            staged = prepare_configurator_update(self.paths, manifest, self._progress)
            schedule_configurator_update(self.paths, staged)
            self.events.put(("restart-update", "更新包已验证，配置器将关闭、替换并重新启动。"))
        self._worker(work)

    def _show_update_dialog(self, info: dict[str, object]) -> None:
        dialog = tk.Toplevel(self)
        dialog.title("安全更新中心")
        dialog.geometry("590x440")
        dialog.resizable(False, False)
        dialog.configure(bg=BG)
        dialog.transient(self)
        dialog.grab_set()
        body = tk.Frame(dialog, bg=CARD, padx=22, pady=18, highlightbackground=LINE, highlightthickness=1)
        body.pack(fill="both", expand=True, padx=14, pady=14)
        tk.Label(body, text="安全更新中心", bg=CARD, fg=TEXT,
                 font=("Microsoft YaHei UI", 16, "bold")).pack(anchor="w")
        tk.Label(body, text="只更新配置器管理的组件；用户原有软件仅显示版本。",
                 bg=CARD, fg=MUTED, font=("Microsoft YaHei UI", 8)).pack(anchor="w", pady=(3, 14))

        configurator = info.get("configurator") if isinstance(info, dict) else {}
        configurator = configurator if isinstance(configurator, dict) else {}
        claude = info.get("claude") if isinstance(info, dict) else {}
        claude = claude if isinstance(claude, dict) else {}
        runtime = info.get("runtime") if isinstance(info, dict) else {}
        runtime = runtime if isinstance(runtime, dict) else {}
        rows = (
            ("配置器", f"当前 {configurator.get('current', APP_VERSION)} · {configurator.get('message', '')}"),
            ("Claude Code", f"当前 {claude.get('current', '未知')} · 最新 {claude.get('latest', '未知')}"),
            ("安装来源", f"{claude.get('source', '未知')} · {claude.get('message', '')}"),
            ("兼容环境", f"Node.js {runtime.get('node', '未知')} · PortableGit {runtime.get('git', '未知')}"),
        )
        for title, value in rows:
            row = tk.Frame(body, bg=SOFT, padx=12, pady=9, highlightbackground=LINE, highlightthickness=1)
            row.pack(fill="x", pady=3)
            tk.Label(row, text=title, width=10, anchor="w", bg=SOFT, fg=TEXT,
                     font=("Microsoft YaHei UI", 9, "bold")).pack(side="left")
            tk.Label(row, text=value, anchor="w", justify="left", wraplength=390,
                     bg=SOFT, fg=MUTED, font=("Microsoft YaHei UI", 8)).pack(side="left", fill="x", expand=True)
        controls = tk.Frame(body, bg=CARD)
        controls.pack(fill="x", side="bottom", pady=(14, 0))
        can_update = bool(claude.get("managed") and claude.get("available"))
        update = tk.Button(
            controls, text="更新受管理 Claude Code" if can_update else "当前没有可一键更新的组件",
            command=lambda: self._update_managed_claude(dialog),
            state="normal" if can_update else "disabled",
            bg=PRIMARY, fg="white", disabledforeground="#aaa7c8", relief="flat", bd=0,
            padx=14, pady=8, font=("Microsoft YaHei UI", 9, "bold"),
        )
        update.pack(side="left")
        app_manifest = configurator.get("manifest")
        if configurator.get("available") and isinstance(app_manifest, dict):
            tk.Button(
                controls, text=f"更新配置器到 {configurator.get('latest')}",
                command=lambda: self._update_configurator(app_manifest, dialog),
                bg=PRIMARY_SOFT, fg=PRIMARY, relief="flat", bd=0,
                padx=14, pady=8, font=("Microsoft YaHei UI", 9, "bold"),
            ).pack(side="left", padx=(8, 0))
        tk.Button(controls, text="关闭", command=dialog.destroy, bg=CARD, fg=MUTED,
                  relief="flat", bd=0, padx=14, pady=8,
                  font=("Microsoft YaHei UI", 9)).pack(side="right")
        dialog.wait_window()

    def _show_install_recovery(self, error: InstallSourceError) -> None:
        dialog = tk.Toplevel(self)
        dialog.title("选择其他安装方案")
        dialog.geometry("640x520")
        dialog.resizable(False, False)
        dialog.configure(bg=BG)
        dialog.transient(self)
        dialog.grab_set()
        body = tk.Frame(dialog, bg=CARD, padx=22, pady=18, highlightbackground=LINE, highlightthickness=1)
        body.pack(fill="both", expand=True, padx=14, pady=14)
        tk.Label(body, text="官方下载未完成，我可以换一种方式继续安装",
                 bg=CARD, fg=TEXT, font=("Microsoft YaHei UI", 14, "bold")).pack(anchor="w")
        tk.Label(body, text="已经完成的步骤会保留；重新选择不会重复破坏现有软件。",
                 bg=CARD, fg=MUTED, font=("Microsoft YaHei UI", 8)).pack(anchor="w", pady=(3, 12))
        details = tk.Text(body, height=8, bg=SOFT, fg=MUTED, relief="flat", wrap="word",
                          padx=10, pady=8, font=("Microsoft YaHei UI", 8))
        details.pack(fill="x")
        for item in error.failures:
            details.insert("end", f"• {item.get('label', item.get('source', '安装源'))}：{item.get('message', '未完成')}\n")
        details.config(state="disabled")
        tk.Label(body, text="请选择下一种方案：", bg=CARD, fg=TEXT,
                 font=("Microsoft YaHei UI", 9, "bold")).pack(anchor="w", pady=(14, 7))
        labels = {
            "npm_mirror": "使用 npm 国内镜像（推荐）",
            "npm_official": "使用 npm 官方源",
            "native": "重试 Anthropic 官方安装",
            "winget": "尝试 WinGet",
        }
        buttons = tk.Frame(body, bg=CARD)
        buttons.pack(fill="x")

        def retry(strategy: str) -> None:
            dialog.destroy()
            self.after(80, lambda: self._install(strategy))

        for index, strategy in enumerate(error.available):
            if strategy not in labels:
                continue
            button = tk.Button(
                buttons, text=labels[strategy], command=lambda value=strategy: retry(value),
                bg=PRIMARY_SOFT if index else PRIMARY, fg=PRIMARY if index else "white",
                activebackground="#dfddff", activeforeground=PRIMARY_DARK,
                relief="flat", bd=0, padx=12, pady=8,
                font=("Microsoft YaHei UI", 8, "bold"), cursor="hand2",
            )
            button.pack(fill="x", pady=3)
        detected_proxy = self.detected_proxies.get("https") or self.detected_proxies.get("http")
        if detected_proxy and not self.use_detected_proxy.get():
            def retry_proxy() -> None:
                self.use_detected_proxy.set(True)
                retry("native")
            tk.Button(
                buttons, text=f"使用检测到的代理重试（{detected_proxy}）", command=retry_proxy,
                bg=PRIMARY_SOFT, fg=PRIMARY, relief="flat", bd=0, padx=12, pady=8,
                font=("Microsoft YaHei UI", 8, "bold"), cursor="hand2",
            ).pack(fill="x", pady=3)
        footer = tk.Frame(body, bg=CARD)
        footer.pack(fill="x", side="bottom", pady=(12, 0))
        tk.Button(footer, text="导出诊断", command=lambda: (dialog.destroy(), self._export_diagnostics()),
                  bg=CARD, fg=PRIMARY, relief="flat", bd=0,
                  font=("Microsoft YaHei UI", 8, "bold")).pack(side="left")
        tk.Button(footer, text="暂停安装", command=dialog.destroy, bg=CARD, fg=MUTED,
                  relief="flat", bd=0, font=("Microsoft YaHei UI", 8)).pack(side="right")
        dialog.wait_window()

    def _export_diagnostics(self) -> None:
        destination = filedialog.asksaveasfilename(
            title="保存脱敏诊断包",
            defaultextension=".zip",
            filetypes=(("ZIP 压缩包", "*.zip"),),
            initialfile=f"ClaudeDeepSeek-诊断-{datetime.now():%Y%m%d-%H%M%S}.zip",
        )
        if not destination:
            return
        def work():
            exported = export_diagnostics(self.paths, Path(destination))
            self.events.put(("info", f"脱敏诊断包已保存：\n{exported}"))
        self._worker(work)

    def _uninstall(self) -> None:
        confirmed = messagebox.askyesno(
            "确认完全回滚",
            "将恢复到运行配置器之前的状态：停止旧版本遗留代理，并删除本次由配置器新增的 PortableGit、受管理 Node/npm、Claude Code、VS Code/扩展、快捷方式、凭据和环境配置。\n\n安装前已经存在的软件和扩展会保留。是否继续？",
        )
        if not confirmed:
            return
        remove_legacy_external = False
        legacy = legacy_external_cleanup_candidates(self.paths)
        if legacy:
            labels = {
                "claude": "Claude Code", "vscode": "VS Code",
                "vscode_extension": "Anthropic VS Code 扩展",
            }
            detected = "、".join(labels.get(name, name) for name in legacy)
            choice = messagebox.askyesnocancel(
                "旧版遗留组件确认",
                "检测到旧版没有所有权记录的外部组件：\n"
                f"{detected}\n\n"
                "旧版无法自动判断它们原本就存在，还是由 V2.8.x 安装。"
                "只有在你确认这台电脑运行旧配置器之前没有这些组件时，才选择“是”并一并删除。\n\n"
                "选择“否”将保留这些外部组件，只清理能够确认属于配置器的内容；选择“取消”停止卸载。",
            )
            if choice is None:
                return
            remove_legacy_external = bool(choice)
        def work():
            self.events.put(("progress", "卸载\n正在安全清理配置器组件…"))
            self.events.put(("uninstalled", uninstall_all(
                self.paths, remove_confirmed_legacy_external=remove_legacy_external,
            )))
        self._worker(work)

    def _poll(self) -> None:
        try:
            while True:
                kind, payload = self.events.get_nowait()
                if kind == "progress":
                    status, _, detail = str(payload).partition("\n")
                    self.status.set(status)
                    self.detail.set(detail)
                    self._update_task_progress(status, detail)
                    self.log.config(state="normal")
                    self.log.insert("end", f"[{datetime.now():%H:%M:%S}] {status}：{detail.replace(chr(10), ' ')}\n")
                    if int(self.log.index("end-1c").split(".")[0]) > 120:
                        self.log.delete("1.0", "21.0")
                    self.log.see("end")
                    self.log.config(state="disabled")
                elif kind == "success":
                    payload = str(payload)
                    self._stop_progress_animation()
                    self.progress_bar.config(mode="determinate")
                    self.progress_value.set(100)
                    self.progress_text.config(text="100%")
                    if self.active_task:
                        self._complete_task(self.active_task)
                    self.current_task_bar.stop()
                    self.current_task_bar.config(mode="determinate")
                    self.current_task_value.set(100)
                    self.task_badge.config(text="✓", bg=SUCCESS)
                    self.task_title.config(text="操作完成")
                    self.task_meta.config(text=f"已完成 {len(self.completed_tasks)} 个步骤")
                    self.task_state.config(text="完成", fg=SUCCESS)
                    self.task_detail.config(text=payload)
                    self.status.set("✓ 完成")
                    self.detail.set(payload)
                    messagebox.showinfo("完成", payload)
                elif kind == "error":
                    payload = str(payload)
                    self._stop_progress_animation()
                    self.progress_bar.config(mode="determinate")
                    self.progress_text.config(text="已停止")
                    self.current_task_bar.stop()
                    self.current_task_bar.config(mode="determinate")
                    self.task_badge.config(text="!", bg=ERROR)
                    self.task_state.config(text="需要处理", fg=ERROR)
                    self.task_detail.config(text=payload)
                    self.status.set("未完成")
                    self.detail.set(payload)
                    messagebox.showerror("遇到问题", payload)
                elif kind == "install-options" and isinstance(payload, InstallSourceError):
                    self._stop_progress_animation()
                    self.progress_bar.config(mode="determinate")
                    self.progress_text.config(text="等待选择")
                    self.task_badge.config(text="!", bg=ERROR)
                    self.task_state.config(text="请选择方案", fg=ERROR)
                    self.status.set("需要选择其他安装方案")
                    self.detail.set(str(payload))
                    self._set_busy(False)
                    self._show_install_recovery(payload)
                elif kind == "updates" and isinstance(payload, dict):
                    self._show_update_dialog(payload)
                elif kind == "restart-update":
                    messagebox.showinfo("准备更新", str(payload))
                    self.destroy()
                    return
                elif kind == "info":
                    messagebox.showinfo("配置器信息", str(payload))
                elif kind == "uninstalled":
                    messagebox.showinfo("卸载完成", str(payload))
                    self.destroy()
                    return
                elif kind == "busy":
                    self._set_busy(payload == "1")
        except queue.Empty:
            pass
        self.after(120, self._poll)


def main() -> None:
    app = App()
    app.mainloop()
