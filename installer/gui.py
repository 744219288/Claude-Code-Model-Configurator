"""Beginner-friendly Chinese Tkinter GUI."""

from __future__ import annotations

import base64
import math
import queue
import re
import struct
import sys
import threading
import tkinter as tk
import webbrowser
import zlib
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
from .providers import DEFAULT_PROVIDER_ID, PROVIDERS, get_provider


BG = "#f5f5f7"
CARD = "#ffffff"
PRIMARY = "#625df5"
PRIMARY_DARK = "#4f4bd8"
PRIMARY_SOFT = "#f1f0ff"
SOFT = "#f8f8fa"
LINE = "#e5e5ea"
TEXT = "#1d1d1f"
MUTED = "#6e6e73"
SUCCESS = "#16835d"
ERROR = "#d23f4b"
SIDEBAR = "#132d5b"
SIDEBAR_DARK = "#0e244c"
SIDEBAR_TEXT = "#e4ebfb"

FLUENT_GLYPHS = {
    "update": "\uf13e",
    "status": "\ue9dd",
    "diagnostics": "\uf725",
    "uninstall": "\uf34d",
    "test": "\ue99a",
    "lock": "\ue78f",
    "check": "\uf298",
    "eye": "\ue5f2",
    "document": "\ue557",
    "chevron": "\uf2a2",
    "play": "\uf606",
}
FLUENT_FALLBACKS = {
    "update": "↻", "status": "◉", "diagnostics": "+", "uninstall": "×",
    "test": "⌁", "lock": "▣", "check": "✓", "eye": "◉",
    "document": "▤", "chevron": "⌄", "play": "▶",
}

PROVIDER_GROUPS = (
    ("deepseek", "DeepSeek", ("deepseek",)),
    ("zhipu", "智谱 GLM", ("zhipu",)),
    ("minimax", "MiniMax", ("minimax",)),
    ("aliyun", "阿里云百炼", ("aliyun-coding", "aliyun-token", "aliyun-payg")),
)
ALIYUN_PLAN_LABELS = {
    "aliyun-coding": "Coding Plan",
    "aliyun-token": "Token Plan",
    "aliyun-payg": "按量付费",
}


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


def load_private_font(path: Path) -> bool:
    if sys.platform != "win32" or not path.is_file():
        return False
    try:
        import ctypes

        return bool(ctypes.windll.gdi32.AddFontResourceExW(str(path), 0x10, 0))
    except Exception:
        return False


def _png_chunk(kind: bytes, data: bytes) -> bytes:
    return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)


def _encode_rgba_png(width: int, height: int, pixels: bytes) -> str:
    rows = b"".join(b"\x00" + pixels[y * width * 4:(y + 1) * width * 4] for y in range(height))
    png = (
        b"\x89PNG\r\n\x1a\n"
        + _png_chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0))
        + _png_chunk(b"IDAT", zlib.compress(rows, 9))
        + _png_chunk(b"IEND", b"")
    )
    return base64.b64encode(png).decode("ascii")


def _rgb(color: str) -> tuple[int, int, int]:
    return tuple(int(color[index:index + 2], 16) for index in (1, 3, 5))


def _render_circle_image(size: int, fill: str, outline: str) -> str:
    center = (size - 1) / 2
    radius = size / 2 - 1.2
    inner = radius - 1.0
    fill_rgb = _rgb(fill)
    outline_rgb = _rgb(outline)
    pixels = bytearray()
    for y in range(size):
        for x in range(size):
            distance = math.hypot(x - center, y - center)
            outer_alpha = max(0.0, min(1.0, radius + 0.7 - distance))
            border_mix = max(0.0, min(1.0, distance - inner + 0.5))
            color = tuple(round(fill_rgb[i] * (1 - border_mix) + outline_rgb[i] * border_mix) for i in range(3))
            pixels.extend((*color, round(255 * outer_alpha)))
    return _encode_rgba_png(size, size, bytes(pixels))


def _render_rounded_square(size: int, fill: str, outline: str) -> str:
    fill_rgb = _rgb(fill)
    outline_rgb = _rgb(outline)
    center = (size - 1) / 2
    half = size / 2 - 1.0
    radius = size * 0.24
    pixels = bytearray()
    for y in range(size):
        for x in range(size):
            qx = abs(x - center) - (half - radius)
            qy = abs(y - center) - (half - radius)
            distance = math.hypot(max(qx, 0.0), max(qy, 0.0)) + min(max(qx, qy), 0.0) - radius
            alpha = max(0.0, min(1.0, 0.75 - distance))
            border_mix = max(0.0, min(1.0, distance + 1.4))
            color = tuple(round(fill_rgb[i] * (1 - border_mix) + outline_rgb[i] * border_mix) for i in range(3))
            pixels.extend((*color, round(255 * alpha)))
    return _encode_rgba_png(size, size, bytes(pixels))


_RING_CACHE: dict[tuple[int, int], str] = {}


def _render_progress_ring(size: int, value: float) -> str:
    cache_key = (size, round(value))
    cached = _RING_CACHE.get(cache_key)
    if cached:
        return cached
    center = (size - 1) / 2
    radius = size * 0.365
    stroke = size * 0.068
    track_rgb = _rgb("#e7e7ed")
    start_rgb = _rgb("#766df7")
    end_rgb = _rgb("#4e7cf6")
    progress_angle = max(0.0, min(360.0, value * 3.6))
    start_point = (center, center - radius)
    end_radians = math.radians(progress_angle - 90)
    end_point = (center + radius * math.cos(end_radians), center + radius * math.sin(end_radians))
    pixels = bytearray()
    for y in range(size):
        for x in range(size):
            dx, dy = x - center, y - center
            distance = math.hypot(dx, dy)
            edge = abs(distance - radius)
            track_alpha = max(0.0, min(1.0, stroke / 2 + 0.75 - edge))
            clockwise_angle = math.degrees(math.atan2(dx, -dy)) % 360
            on_arc = value >= 100 or clockwise_angle <= progress_angle
            round_cap = (
                value > 0 and (
                    math.hypot(x - start_point[0], y - start_point[1]) <= stroke / 2
                    or math.hypot(x - end_point[0], y - end_point[1]) <= stroke / 2
                )
            )
            progress_alpha = track_alpha if value > 0 and (on_arc or round_cap) else 0.0
            if progress_alpha:
                ratio = min(1.0, clockwise_angle / max(1.0, progress_angle))
                color = tuple(round(start_rgb[i] * (1 - ratio) + end_rgb[i] * ratio) for i in range(3))
                pixels.extend((*color, round(255 * progress_alpha)))
            else:
                pixels.extend((*track_rgb, round(255 * track_alpha)))
    encoded = _encode_rgba_png(size, size, bytes(pixels))
    _RING_CACHE[cache_key] = encoded
    return encoded


class ModernScrollbar(tk.Canvas):
    """Slim, arrowless scrollbar matching the application's visual language."""

    def __init__(self, parent: tk.Widget, command, *, background: str = BG) -> None:
        super().__init__(
            parent, width=12, bg=background, highlightthickness=0, bd=0,
            cursor="hand2", takefocus=0,
        )
        self.command = command
        self.first = 0.0
        self.last = 1.0
        self._thumb = (8.0, 48.0)
        self._drag_offset: float | None = None
        self.bind("<Configure>", lambda _event: self._draw())
        self.bind("<Button-1>", self._press)
        self.bind("<B1-Motion>", self._drag)
        self.bind("<ButtonRelease-1>", lambda _event: setattr(self, "_drag_offset", None))

    def set(self, first, last) -> None:
        self.first = max(0.0, min(1.0, float(first)))
        self.last = max(self.first, min(1.0, float(last)))
        self._draw()

    def _geometry(self) -> tuple[float, float, float, float]:
        top = 10.0
        bottom = max(top + 1.0, float(self.winfo_height()) - 10.0)
        track = bottom - top
        visible = max(0.0, min(1.0, self.last - self.first))
        thumb_length = max(44.0, track * visible)
        thumb_length = min(track, thumb_length)
        available = max(0.0, track - thumb_length)
        denominator = max(0.0001, 1.0 - visible)
        thumb_top = top + available * min(1.0, self.first / denominator)
        return top, bottom, thumb_top, thumb_top + thumb_length

    def _draw(self) -> None:
        self.delete("all")
        width = max(1, self.winfo_width())
        top, bottom, thumb_top, thumb_bottom = self._geometry()
        center = width / 2
        self.create_line(center, top, center, bottom, fill="#ececf1", width=3, capstyle="round")
        self.create_line(
            center, thumb_top, center, thumb_bottom,
            fill="#a8a8b0", activefill="#7d7d86", width=5, capstyle="round",
        )
        self._thumb = (thumb_top, thumb_bottom)

    def _press(self, event) -> None:
        thumb_top, thumb_bottom = self._thumb
        if thumb_top <= event.y <= thumb_bottom:
            self._drag_offset = event.y - thumb_top
            return
        self.command("scroll", -1 if event.y < thumb_top else 1, "pages")

    def _drag(self, event) -> None:
        if self._drag_offset is None:
            return
        top, bottom, thumb_top, thumb_bottom = self._geometry()
        available = max(1.0, (bottom - top) - (thumb_bottom - thumb_top))
        fraction = (event.y - self._drag_offset - top) / available
        self.command("moveto", max(0.0, min(1.0, fraction)))


class ProgressRing(tk.Canvas):
    """Anti-aliased circular total-progress indicator."""

    def __init__(self, parent: tk.Widget) -> None:
        super().__init__(parent, width=120, height=120, bg=CARD, highlightthickness=0, bd=0)
        self.value = 0.0
        self.display = "0%"
        self._ring_image: tk.PhotoImage | None = None
        self._draw()

    def set(self, value: float | None, display: str | None = None) -> None:
        if value is not None:
            self.value = max(0.0, min(100.0, float(value)))
        self.display = display if display is not None else f"{round(self.value)}%"
        self._draw()

    def _draw(self) -> None:
        self.delete("all")
        size = 120
        self._ring_image = tk.PhotoImage(data=_render_progress_ring(size, self.value))
        self.create_image(size / 2, size / 2, image=self._ring_image)
        self.create_text(
            size / 2, size / 2 - 1, text=self.display, fill=TEXT,
            font=("Segoe UI Variable", 18, "bold"),
        )


class IconButton(tk.Frame):
    """Soft action card using anti-aliased official/font icons."""

    def __init__(
        self, parent: tk.Widget, text: str, command, icon: str, *,
        danger: bool = False, quiet: bool = True, image: tk.PhotoImage | None = None,
        icon_font: str | None = None, bordered: bool = True, prominent: bool = False,
    ) -> None:
        self.normal_bg = PRIMARY if prominent else "#f3f3f6" if quiet and bordered else CARD if quiet else PRIMARY_SOFT
        self.hover_bg = PRIMARY_DARK if prominent else "#fff1f2" if danger else ("#eaeaef" if quiet else "#e8e6ff")
        self.normal_fg = "white" if prominent else ERROR if danger else (TEXT if quiet else PRIMARY_DARK)
        self.icon_color = "white" if prominent else ERROR if danger else PRIMARY
        super().__init__(
            parent, bg=self.normal_bg, padx=14, pady=10,
            highlightbackground=LINE, highlightthickness=0, cursor="hand2",
        )
        self.command = command
        self.state = "normal"
        self._image = image
        if image is not None:
            self.icon_widget: tk.Widget = tk.Label(self, image=image, bg=self.normal_bg, bd=0)
        else:
            glyph = FLUENT_GLYPHS.get(icon, "") if icon_font else FLUENT_FALLBACKS.get(icon, "•")
            self.icon_widget = tk.Label(
                self, text=glyph, bg=self.normal_bg,
                fg=self.icon_color, bd=0, cursor="hand2",
                font=(icon_font or "Segoe UI Symbol", 18),
            )
        self.icon_widget.pack(side="left", padx=(0, 9))
        self.label = tk.Label(
            self, text=text, bg=self.normal_bg, fg=self.normal_fg,
            font=("Microsoft YaHei UI", 9), cursor="hand2",
        )
        self.label.pack(side="left")
        for widget in (self, self.icon_widget, self.label):
            widget.bind("<Button-1>", self._activate)
            widget.bind("<Enter>", lambda _event: self._paint(self.hover_bg))
            widget.bind("<Leave>", lambda _event: self._paint(self.normal_bg))

    def _paint(self, background: str) -> None:
        if self.state == "disabled":
            return
        self.configure(bg=background)
        self.icon_widget.configure(bg=background)
        self.label.configure(bg=background)

    def _activate(self, _event=None) -> None:
        if self.state == "normal":
            self.command()

    def config(self, **kwargs) -> None:
        state = kwargs.pop("state", None)
        text = kwargs.pop("text", None)
        if state is not None:
            self.state = state
            self.label.configure(fg="#a9afbb" if state == "disabled" else self.normal_fg)
            if self._image is None:
                self.icon_widget.configure(fg="#c4c4c8" if state == "disabled" else self.icon_color)
            self.configure(cursor="arrow" if state == "disabled" else "hand2")
        if text is not None:
            self.label.configure(text=text)
        if kwargs:
            self.configure(**kwargs)


class ModernCheckbutton(tk.Frame):
    """Small anti-aliased checkbox with a Fluent checkmark."""

    def __init__(
        self, parent: tk.Widget, text: str, variable: tk.BooleanVar, *,
        icon_font: str | None, state: str = "normal",
    ) -> None:
        super().__init__(parent, bg=CARD, cursor="hand2")
        self.variable = variable
        self.state = state
        self.icon_font = icon_font
        self.off_image = tk.PhotoImage(data=_render_rounded_square(20, "#ffffff", "#c7c7cc"))
        self.on_image = tk.PhotoImage(data=_render_rounded_square(20, PRIMARY, PRIMARY))
        self.disabled_image = tk.PhotoImage(data=_render_rounded_square(20, "#f0f0f2", "#d8d8dc"))
        self.box = tk.Label(self, bg=CARD, bd=0, compound="center", cursor="hand2")
        self.box.pack(side="left")
        self.label = tk.Label(
            self, text=text, bg=CARD, fg=MUTED, bd=0, cursor="hand2",
            font=("Microsoft YaHei UI", 8),
        )
        self.label.pack(side="left", padx=(7, 0))
        for widget in (self, self.box, self.label):
            widget.bind("<Button-1>", self._toggle)
        self.variable.trace_add("write", lambda *_args: self._refresh())
        self._refresh()

    def _toggle(self, _event=None) -> None:
        if self.state == "normal":
            self.variable.set(not self.variable.get())

    def _refresh(self) -> None:
        checked = bool(self.variable.get())
        if self.state == "disabled":
            self.box.config(image=self.disabled_image, text="")
            self.label.config(fg="#aeaeb2")
            return
        glyph = FLUENT_GLYPHS["check"] if self.icon_font else "✓"
        self.box.config(
            image=self.on_image if checked else self.off_image,
            text=glyph if checked else "", fg="white",
            font=(self.icon_font or "Segoe UI Symbol", 10),
        )
        self.label.config(fg=TEXT if checked else MUTED)

    def config(self, **kwargs) -> None:
        state = kwargs.pop("state", None)
        if state is not None:
            self.state = state
            self.configure(cursor="arrow" if state == "disabled" else "hand2")
            self._refresh()
        if kwargs:
            self.configure(**kwargs)

INSTALL_TASKS = (
    ("系统检查", "系统检查"),
    ("安装包完整性", "验证并固定完整安装资源"),
    ("离线组件", "检查 Git 与 Node.js 离线包"),
    ("安全保存", "安全保存 API Key"),
    ("直连网关", "配置所选厂商 Anthropic 直连"),
    ("Git Bash", "准备隔离的 Git for Windows"),
    ("安装源检测", "检测国内与官方安装源"),
    ("npm 运行环境", "准备受管理 Node.js/npm"),
    ("Claude Code", "安装 Claude Code"),
    ("VS Code", "安装 VS Code"),
    ("VS Code 扩展", "安装 Claude Code 扩展"),
    ("配置", "生成并保存配置"),
    ("终端入口", "配置终端 claude 命令"),
    ("连接测试", "测试模型服务连接"),
    ("桌面入口", "创建 Claude Code + 国产模型快捷方式"),
)


class App(tk.Tk):
    def __init__(self) -> None:
        enable_high_dpi()
        super().__init__()
        self.icon_font_family: str | None = None
        self.paths = Paths.default()
        self.events: queue.Queue[tuple[str, object]] = queue.Queue()
        self.title(f"Claude Code 国产模型配置器 V{APP_VERSION}")
        self.geometry("1120x760")
        self.minsize(900, 640)
        self.configure(bg=BG)
        initial_provider = DEFAULT_PROVIDER_ID
        initial_model = DEFAULT_MODEL
        try:
            saved = system_status(self.paths)
            candidate_provider = str(saved.get("provider") or DEFAULT_PROVIDER_ID)
            if candidate_provider in PROVIDERS:
                initial_provider = candidate_provider
                candidate_model = str(saved.get("model") or "")
                if any(item.id == candidate_model for item in PROVIDERS[initial_provider].models):
                    initial_model = candidate_model
                else:
                    initial_model = PROVIDERS[initial_provider].default_model
        except Exception:
            pass
        self.provider = tk.StringVar(value=initial_provider)
        self.provider_brand = tk.StringVar(value=self._brand_for_provider(initial_provider))
        self.model = tk.StringVar(value=initial_model)
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
        self._key_drafts: dict[str, str] = {}
        self._active_provider_id: str | None = None
        self.provider_buttons: dict[str, tk.Button] = {}
        self.plan_buttons: dict[str, tk.Button] = {}
        self.model_rows: dict[str, tuple[tk.Frame, tk.Frame, tk.Button]] = {}
        self.sidebar_buttons: dict[str, tk.Button] = {}
        self.sidebar_rows: dict[str, tuple[tk.Frame, tk.Frame, tk.Label]] = {}
        self.app_icon_full: tk.PhotoImage | None = None
        self.app_icon_large: tk.PhotoImage | None = None
        self.app_icon_small: tk.PhotoImage | None = None
        self.vscode_icon_full: tk.PhotoImage | None = None
        self.vscode_icon_small: tk.PhotoImage | None = None
        self.ui_icons: dict[str, tk.PhotoImage] = {}
        self.step_badge_active: tk.PhotoImage | None = None
        self.step_badge_idle: tk.PhotoImage | None = None
        self.step_badge_success: tk.PhotoImage | None = None
        self.step_badge_error: tk.PhotoImage | None = None
        self._build()
        self._refresh_provider(load_key=True)
        self.after(20, self._apply_window_style)
        self.after(30, self._center_window)
        self.after(120, self._poll)
        try:
            if read_api_key(self.provider.get()):
                self.detail.set("已在 Windows 凭据管理器中找到保存的 API Key，可直接测试连接。")
        except CredentialError:
            pass

    def _load_brand_icon(self) -> None:
        try:
            self.app_icon_full = tk.PhotoImage(file=str(resource_path("assets/app_icon.png")))
            large_factor = max(1, round(self.app_icon_full.width() / 60))
            small_factor = max(1, round(self.app_icon_full.width() / 24))
            self.app_icon_large = self.app_icon_full.subsample(large_factor, large_factor)
            self.app_icon_small = self.app_icon_full.subsample(small_factor, small_factor)
            self.iconphoto(True, self.app_icon_full)
            icon_file = resource_path("assets/app_icon.ico")
            if icon_file.is_file():
                self.iconbitmap(default=str(icon_file))
            for name in (
                "update", "status", "diagnostics", "uninstall", "test",
                "play-white", "lock-white", "check-green", "vscode",
            ):
                self.ui_icons[name] = tk.PhotoImage(file=str(resource_path(f"assets/ui/{name}.png")))
            self.vscode_icon_full = self.ui_icons["vscode"]
            self.vscode_icon_small = self.ui_icons["vscode"]
            self.step_badge_active = tk.PhotoImage(data=_render_circle_image(32, "#7167f5", "#8d85ff"))
            self.step_badge_idle = tk.PhotoImage(data=_render_circle_image(32, "#365382", "#4b6691"))
            self.step_badge_success = tk.PhotoImage(data=_render_circle_image(32, SUCCESS, "#45a985"))
            self.step_badge_error = tk.PhotoImage(data=_render_circle_image(32, ERROR, "#ed7a84"))
        except (tk.TclError, OSError):
            self.app_icon_full = None
            self.app_icon_large = None
            self.app_icon_small = None
            self.vscode_icon_full = None
            self.vscode_icon_small = None
            self.ui_icons.clear()
            self.step_badge_active = None
            self.step_badge_idle = None
            self.step_badge_success = None
            self.step_badge_error = None

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

    @staticmethod
    def _brand_for_provider(provider_id: str) -> str:
        for brand_id, _label, provider_ids in PROVIDER_GROUPS:
            if provider_id in provider_ids:
                return brand_id
        return "deepseek"

    @staticmethod
    def _card(parent: tk.Widget, padx: int = 18, pady: int = 16) -> tk.Frame:
        return tk.Frame(
            parent, bg=CARD, padx=padx, pady=pady,
            highlightbackground=LINE, highlightthickness=1,
        )

    def _toolbar_button(
        self, parent: tk.Widget, text: str, command, icon: str, *, danger: bool = False,
    ) -> IconButton:
        return IconButton(
            parent, text, command, icon, danger=danger,
            image=self.ui_icons.get(icon), bordered=False,
        )

    def _build(self) -> None:
        self._load_brand_icon()
        style = ttk.Style(self)
        style.theme_use("clam")
        style.configure("Overall.Horizontal.TProgressbar", troughcolor="#e9eaf2", background=PRIMARY, bordercolor="#e9eaf2", lightcolor=PRIMARY, darkcolor=PRIMARY, thickness=8)
        style.configure("Current.Horizontal.TProgressbar", troughcolor="#e5e6f0", background="#746ff0", bordercolor="#e5e6f0", lightcolor="#746ff0", darkcolor="#746ff0", thickness=7)
        header = tk.Frame(self, bg=CARD, height=104, highlightbackground=LINE, highlightthickness=1)
        header.pack(side="top", fill="x")
        header.pack_propagate(False)
        brand = tk.Frame(header, bg=CARD)
        brand.pack(side="left", fill="y", padx=(22, 0))
        if self.app_icon_large is not None:
            tk.Label(brand, image=self.app_icon_large, bg=CARD, bd=0).pack(side="left", pady=20)
        else:
            tk.Label(brand, text="C/", bg=PRIMARY, fg="white", width=3, height=1,
                     font=("Segoe UI", 15, "bold")).pack(side="left", pady=24)
        brand_text = tk.Frame(brand, bg=CARD)
        brand_text.pack(side="left", padx=(14, 0), pady=18)
        title_row = tk.Frame(brand_text, bg=CARD)
        title_row.pack(anchor="w")
        tk.Label(title_row, text="Claude Code 国产模型配置器", bg=CARD, fg=TEXT,
                 font=("Microsoft YaHei UI", 16, "bold")).pack(side="left")
        safety = tk.Frame(title_row, bg="#edf8f2", padx=7, pady=3)
        safety.pack(side="left", padx=(12, 0))
        tk.Label(
            safety, image=self.ui_icons.get("check-green"),
            bg="#edf8f2", bd=0,
        ).pack(side="left")
        tk.Label(safety, text="安全直连", bg="#edf8f2", fg=SUCCESS,
                 font=("Microsoft YaHei UI", 8, "bold")).pack(side="left", padx=(3, 0))
        tk.Label(brand_text, text="选择厂商、模型并安全配置 Claude Code", bg=CARD, fg=MUTED,
                 font=("Microsoft YaHei UI", 9)).pack(anchor="w", pady=(3, 0))

        command_bar = tk.Frame(
            header, bg="#fafbfe", padx=3, pady=3,
            highlightbackground=LINE, highlightthickness=1,
        )
        command_bar.pack(side="right", padx=(10, 22), pady=21)
        self.update_button = self._toolbar_button(command_bar, "检查更新", self._check_updates, "update")
        self.update_button.pack(side="left")
        self.status_button = self._toolbar_button(command_bar, "状态", self._show_status, "status")
        self.status_button.pack(side="left", padx=(3, 0))
        self.diagnostics_button = self._toolbar_button(command_bar, "诊断", self._export_diagnostics, "diagnostics")
        self.diagnostics_button.pack(side="left", padx=(3, 0))
        self.uninstall_button = self._toolbar_button(command_bar, "卸载", self._uninstall, "uninstall", danger=True)
        self.uninstall_button.pack(side="left", padx=(3, 0))

        workspace = tk.Frame(self, bg=BG)
        workspace.pack(fill="both", expand=True)

        sidebar = tk.Frame(workspace, bg=SIDEBAR, width=222)
        sidebar.pack(side="left", fill="y")
        sidebar.pack_propagate(False)
        tk.Label(sidebar, text=f"V{APP_VERSION}", bg=SIDEBAR, fg="#afc1e8",
                 font=("Segoe UI", 8)).pack(anchor="w", padx=28, pady=(22, 12))
        nav = tk.Frame(sidebar, bg=SIDEBAR)
        nav.pack(fill="x")
        for index, (key, label) in enumerate((
            ("config", "模型配置"), ("progress", "安装进度"), ("maintenance", "运行与维护"),
        ), start=1):
            selected = key == "config"
            row = tk.Frame(nav, bg="#203f75" if selected else SIDEBAR, height=68)
            row.pack(fill="x")
            row.pack_propagate(False)
            accent = tk.Frame(row, bg="#8175ff" if selected else SIDEBAR, width=3)
            accent.pack(side="left", fill="y", pady=13)
            badge_image = self.step_badge_active if selected else self.step_badge_idle
            badge = tk.Label(
                row, text=str(index), image=badge_image, compound="center",
                bg=row.cget("bg"), fg="white", bd=0, cursor="hand2",
                font=("Segoe UI Variable", 9, "bold"),
            )
            badge.pack(side="left", padx=(14, 5))
            button = tk.Button(
                row, text=label, anchor="w", relief="flat", bd=0,
                bg="#203f75" if selected else SIDEBAR,
                fg="white" if selected else "#aebdde",
                activebackground="#203f75", activeforeground="white",
                padx=8, pady=15, cursor="hand2",
                font=("Microsoft YaHei UI", 10, "bold" if selected else "normal"),
                command=lambda target=key: self._navigate_sidebar(target),
            )
            button.pack(side="left", fill="both", expand=True)
            for widget in (row, accent, badge):
                widget.bind("<Button-1>", lambda _event, target=key: self._navigate_sidebar(target))
            self.sidebar_buttons[key] = button
            self.sidebar_rows[key] = (row, accent, badge)
        side_footer = tk.Frame(sidebar, bg=SIDEBAR_DARK, padx=24, pady=17)
        side_footer.pack(side="bottom", fill="x")
        key_security = tk.Frame(side_footer, bg=SIDEBAR_DARK)
        key_security.pack(fill="x")
        tk.Label(
            key_security, image=self.ui_icons.get("lock-white"),
            bg=SIDEBAR_DARK, bd=0,
        ).pack(side="left")
        tk.Label(key_security, text="API Key 仅保存在本机", bg=SIDEBAR_DARK,
                 fg=SIDEBAR_TEXT, font=("Microsoft YaHei UI", 8, "bold")).pack(side="left", padx=(6, 0))
        tk.Label(side_footer, text="Windows 凭据管理器 · 相互隔离", bg=SIDEBAR_DARK,
                 fg="#91a6d2", font=("Microsoft YaHei UI", 7)).pack(anchor="w", pady=(4, 0))

        stage = tk.Frame(workspace, bg=BG)
        stage.pack(side="left", fill="both", expand=True)

        footer = tk.Frame(stage, bg=CARD, height=76, padx=20, pady=12,
                          highlightbackground=LINE, highlightthickness=1)
        footer.pack(side="bottom", fill="x")
        footer.pack_propagate(False)
        self.test_button = self._action_button(footer, "测试连接", self._test, "test", quiet=True)
        self.test_button.pack(side="left", fill="y", ipadx=15)
        self.launch_vscode_button = self._action_button(
            footer, "启动 VS Code", lambda: self._launch(launch_vscode), "vscode", quiet=True,
        )
        self.launch_vscode_button.pack(side="left", fill="y", padx=(10, 0), ipadx=12)
        self.launch_claude_button = self._action_button(
            footer, "启动 Claude Code", lambda: self._launch(launch_claude), "claude", quiet=True,
        )
        self.launch_claude_button.pack(side="left", fill="y", padx=(10, 0), ipadx=12)
        self.install_button = IconButton(
            footer, "开始安装并配置", self._install, "play",
            image=self.ui_icons.get("play-white"), bordered=False, prominent=True,
        )
        self.install_button.pack(side="right", fill="y")

        scroll_host = tk.Frame(stage, bg=BG)
        scroll_host.pack(side="top", fill="both", expand=True)
        self.content_canvas = tk.Canvas(scroll_host, bg=BG, highlightthickness=0, bd=0)
        self.content_scrollbar = ModernScrollbar(scroll_host, command=self.content_canvas.yview)
        self.content_canvas.configure(yscrollcommand=self.content_scrollbar.set)
        self.content_scrollbar.pack(side="right", fill="y")
        self.content_canvas.pack(side="left", fill="both", expand=True)
        self.scroll_content = tk.Frame(self.content_canvas, bg=BG, padx=24, pady=20)
        self.content_window = self.content_canvas.create_window(
            (0, 0), window=self.scroll_content, anchor="nw",
        )
        self.scroll_content.bind("<Configure>", self._update_scroll_region)
        self.content_canvas.bind("<Configure>", self._resize_scroll_content)
        self.content_canvas.bind("<Enter>", self._enable_mousewheel)
        self.content_canvas.bind("<Leave>", self._disable_mousewheel)

        heading = tk.Frame(self.scroll_content, bg=BG)
        heading.pack(fill="x", pady=(0, 12))
        tk.Label(heading, text="模型服务配置", bg=BG, fg=TEXT,
                 font=("Microsoft YaHei UI", 15, "bold")).pack(side="left")
        tk.Label(heading, text="厂商、模型和密钥相互联动", bg=BG, fg=MUTED,
                 font=("Microsoft YaHei UI", 8)).pack(side="left", padx=(12, 0), pady=(5, 0))

        self.config_card = self._card(self.scroll_content)
        self.config_card.pack(fill="x", pady=(0, 12))
        tk.Label(self.config_card, text="选择模型服务商", bg=CARD, fg=TEXT,
                 font=("Microsoft YaHei UI", 10, "bold")).grid(row=0, column=0, sticky="w")
        self.provider_segment = tk.Frame(self.config_card, bg="#f1f3f7", padx=2, pady=2)
        self.provider_segment.grid(row=1, column=0, sticky="ew", pady=(9, 8))
        self.config_card.grid_columnconfigure(0, weight=1)
        for column, (brand_id, label, _provider_ids) in enumerate(PROVIDER_GROUPS):
            self.provider_segment.grid_columnconfigure(column, weight=1, uniform="provider")
            button = tk.Button(
                self.provider_segment, text=label, relief="flat", bd=0, cursor="hand2",
                padx=10, pady=8, font=("Microsoft YaHei UI", 9),
                command=lambda selected=brand_id: self._select_provider_brand(selected),
            )
            button.grid(row=0, column=column, sticky="ew", padx=1)
            self.provider_buttons[brand_id] = button

        self.plan_frame = tk.Frame(self.config_card, bg=CARD)
        self.plan_frame.grid(row=2, column=0, sticky="ew", pady=(1, 7))
        tk.Label(self.plan_frame, text="百炼套餐", bg=CARD, fg=MUTED,
                 font=("Microsoft YaHei UI", 8)).pack(side="left", padx=(2, 8))
        for provider_id, label in ALIYUN_PLAN_LABELS.items():
            button = tk.Button(
                self.plan_frame, text=label, relief="flat", bd=0, cursor="hand2",
                padx=12, pady=5, font=("Microsoft YaHei UI", 8),
                command=lambda selected=provider_id: self._select_aliyun_plan(selected),
            )
            button.pack(side="left", padx=(0, 6))
            self.plan_buttons[provider_id] = button

        self.provider_info = tk.Label(
            self.config_card, text="", bg="#f2faf6", fg=SUCCESS, anchor="w",
            padx=12, pady=7, font=("Microsoft YaHei UI", 8),
        )
        self.provider_info.grid(row=3, column=0, sticky="ew", pady=(0, 11))
        tk.Frame(self.config_card, bg=LINE, height=1).grid(row=4, column=0, sticky="ew", pady=(0, 10))
        tk.Label(self.config_card, text="选择模型", bg=CARD, fg=TEXT,
                 font=("Microsoft YaHei UI", 10, "bold")).grid(row=5, column=0, sticky="w")
        self.model_list = tk.Frame(self.config_card, bg=CARD)
        self.model_list.grid(row=6, column=0, sticky="ew", pady=(8, 0))

        key_card = self._card(self.scroll_content)
        key_card.pack(fill="x", pady=(0, 12))
        key_heading = tk.Frame(key_card, bg=CARD)
        key_heading.pack(fill="x")
        self.key_label = tk.Label(key_heading, text="API Key", bg=CARD, fg=TEXT,
                                  font=("Microsoft YaHei UI", 10, "bold"))
        self.key_label.pack(side="left")
        self.key_saved_label = tk.Label(key_heading, text="仅保存在本机凭据管理器", bg=CARD,
                                        fg=SUCCESS, font=("Microsoft YaHei UI", 8))
        self.key_saved_label.pack(side="right")
        key_row = tk.Frame(key_card, bg=CARD)
        key_row.pack(fill="x", pady=(9, 0))
        entry_border = tk.Frame(key_row, bg=LINE, padx=1, pady=1)
        entry_border.pack(side="left", fill="x", expand=True)
        self.key_entry = tk.Entry(
            entry_border, textvariable=self.key, show="●", relief="flat", bd=0,
            bg="#fbfcfe", fg=TEXT, insertbackground=TEXT, font=("Segoe UI", 10),
        )
        self.key_entry.pack(fill="x", ipady=9, padx=11)
        self.show_key = tk.BooleanVar(value=False)
        self.key_visibility_button = tk.Button(
            key_row, text="显示", command=self._toggle_key_button, bg=CARD, fg=MUTED,
            activebackground="#f1f3f8", activeforeground=TEXT, relief="flat", bd=0,
            padx=10, pady=7, cursor="hand2", font=("Microsoft YaHei UI", 8),
        )
        self.key_visibility_button.pack(side="left", padx=(8, 0))
        self.key_docs_button = tk.Button(
            key_row, text="获取 API Key  ↗", command=self._open_provider_docs,
            bg=CARD, fg=PRIMARY, activebackground=PRIMARY_SOFT, activeforeground=PRIMARY_DARK,
            relief="flat", bd=0, padx=10, pady=7, cursor="hand2",
            font=("Microsoft YaHei UI", 8),
        )
        self.key_docs_button.pack(side="left", padx=(4, 0))

        options_card = self._card(self.scroll_content, padx=16, pady=11)
        options_card.pack(fill="x", pady=(0, 12))
        tk.Label(options_card, text="安装选项（可选）", bg=CARD, fg=TEXT,
                 font=("Microsoft YaHei UI", 9, "bold")).pack(side="left", padx=(0, 18))
        ModernCheckbutton(
            options_card, "同时安装 VS Code 与 Claude 扩展",
            self.install_vscode, icon_font=self.icon_font_family,
        ).pack(side="left")
        detected_proxy = self.detected_proxies.get("https") or self.detected_proxies.get("http")
        proxy_text = f"使用系统代理 {detected_proxy}" if detected_proxy else "未检测到系统代理"
        self.proxy_check = ModernCheckbutton(
            options_card, proxy_text, self.use_detected_proxy,
            icon_font=self.icon_font_family,
            state="normal" if detected_proxy else "disabled",
        )
        self.proxy_check.pack(side="right")

        self.progress_card = self._card(self.scroll_content)
        self.progress_card.pack(fill="x", pady=(0, 20))
        progress_head = tk.Frame(self.progress_card, bg=CARD)
        progress_head.pack(fill="x")
        tk.Label(progress_head, text="安装与配置进度", bg=CARD, fg=TEXT,
                 font=("Microsoft YaHei UI", 11, "bold")).pack(side="left")
        status_strip = tk.Frame(self.progress_card, bg="#f7f8fc", padx=12, pady=8)
        status_strip.pack(fill="x", pady=(10, 12))
        tk.Label(status_strip, textvariable=self.status, bg="#f7f8fc", fg=PRIMARY,
                 font=("Microsoft YaHei UI", 9, "bold")).pack(side="left")
        tk.Label(status_strip, textvariable=self.detail, bg="#f7f8fc", fg=MUTED,
                 anchor="w", font=("Microsoft YaHei UI", 8)).pack(side="left", padx=(12, 0), fill="x", expand=True)

        progress_body = tk.Frame(self.progress_card, bg=CARD)
        progress_body.pack(fill="x")
        progress_body.grid_columnconfigure(0, weight=0)
        progress_body.grid_columnconfigure(1, weight=3)
        progress_body.grid_columnconfigure(2, weight=2)
        summary_panel = tk.Frame(progress_body, bg=CARD, padx=4, pady=2)
        summary_panel.grid(row=0, column=0, sticky="nsew", padx=(0, 14))
        self.progress_ring = ProgressRing(summary_panel)
        self.progress_ring.pack(anchor="center")
        self.progress_text = tk.Label(
            summary_panel, text="等待开始", bg=CARD, fg=PRIMARY,
            font=("Microsoft YaHei UI", 8, "bold"),
        )
        self.progress_text.pack(pady=(2, 7))
        tk.Label(summary_panel, text="总体进度", bg=CARD, fg=MUTED,
                 font=("Microsoft YaHei UI", 8)).pack(anchor="w", fill="x")
        self.progress_bar = ttk.Progressbar(
            summary_panel, variable=self.progress_value, maximum=100,
            mode="determinate", style="Overall.Horizontal.TProgressbar", length=125,
        )
        self.progress_bar.pack(fill="x", pady=(5, 0))
        self.progress_value.trace_add("write", self._sync_progress_ring)
        self.task_panel = tk.Frame(
            progress_body, bg=SOFT, padx=14, pady=12,
            highlightbackground=LINE, highlightthickness=1,
        )
        self.task_panel.grid(row=0, column=1, sticky="nsew", padx=(0, 8))
        task_top = tk.Frame(self.task_panel, bg=SOFT)
        task_top.pack(fill="x")
        self.task_badge = tk.Label(
            task_top, text="…", image=self.step_badge_active, compound="center",
            bg=SOFT, fg="white", bd=0, font=("Segoe UI Variable", 9, "bold"),
        )
        self.task_badge.pack(side="left", padx=(0, 10))
        task_names = tk.Frame(task_top, bg=SOFT)
        task_names.pack(side="left", fill="x", expand=True)
        self.task_title = tk.Label(task_names, text="等待开始", bg=SOFT, fg=TEXT, anchor="w",
                                   font=("Microsoft YaHei UI", 10, "bold"))
        self.task_title.pack(fill="x")
        self.task_meta = tk.Label(task_names, text="输入 API Key 后点击开始安装并配置", bg=SOFT,
                                  fg=MUTED, anchor="w", font=("Microsoft YaHei UI", 8))
        self.task_meta.pack(fill="x", pady=(2, 0))
        self.task_state = tk.Label(task_top, text="准备就绪", bg=SOFT, fg=PRIMARY,
                                   font=("Microsoft YaHei UI", 8, "bold"))
        self.task_state.pack(side="right", padx=(10, 0))
        self.current_task_value = tk.DoubleVar(value=0)
        self.current_task_bar = ttk.Progressbar(
            self.task_panel, variable=self.current_task_value, maximum=100,
            mode="determinate", style="Current.Horizontal.TProgressbar",
        )
        self.current_task_bar.pack(fill="x", pady=(12, 8))
        self.task_detail = tk.Label(
            self.task_panel, text="安装到哪一步，就在这里显示哪一步。", bg=SOFT, fg=MUTED,
            anchor="w", justify="left", wraplength=430, font=("Microsoft YaHei UI", 8),
        )
        self.task_detail.pack(fill="x")
        milestones = tk.Frame(self.task_panel, bg=SOFT)
        milestones.pack(fill="x", pady=(12, 0))
        self.milestone_labels: dict[str, tk.Label] = {}
        for milestone, label in (
            ("系统检查", "系统检查"), ("Git Bash", "Git Bash"),
            ("Claude Code", "Claude Code"), ("桌面入口", "创建桌面快捷方式"),
        ):
            item = tk.Label(
                milestones, text=f"○  {label}", bg=SOFT, fg="#8992a4",
                anchor="w", font=("Microsoft YaHei UI", 8),
            )
            item.pack(fill="x", pady=2)
            self.milestone_labels[milestone] = item

        activity = tk.Frame(progress_body, bg=SOFT, padx=10, pady=9,
                            highlightbackground=LINE, highlightthickness=1)
        activity.grid(row=0, column=2, sticky="nsew")
        tk.Label(activity, text="实时活动", bg=SOFT, fg=TEXT,
                 font=("Microsoft YaHei UI", 9, "bold")).pack(anchor="w", pady=(0, 5))
        log_wrap = tk.Frame(activity, bg=SOFT)
        log_wrap.pack(fill="both", expand=True)
        log_scroll = ModernScrollbar(log_wrap, command=lambda *args: self.log.yview(*args), background=SOFT)
        log_scroll.pack(side="right", fill="y")
        self.log = tk.Text(
            log_wrap, height=6, bg=SOFT, fg="#596276", relief="flat", wrap="word",
            font=("Microsoft YaHei UI", 8), padx=4, pady=2, state="disabled",
            yscrollcommand=log_scroll.set,
        )
        self.log.pack(side="left", fill="both", expand=True)
        self.log.config(state="normal")
        self.log.insert("end", "[提示] 等待开始。安装步骤和检测结果会实时显示在这里。\n")
        self.log.config(state="disabled")

        self.log_toggle = tk.Button(
            self.progress_card, text="▤  查看详细日志  ⌄", command=self._toggle_log_height,
            bg=CARD, fg=TEXT, activebackground="#f3f4f8", activeforeground=TEXT,
            anchor="w", relief="flat", bd=0, padx=4, pady=7, cursor="hand2",
            font=("Microsoft YaHei UI", 8),
        )
        self.log_toggle.pack(fill="x", pady=(10, 0))

    def _refresh_provider(self, load_key: bool) -> None:
        provider_id = self.provider.get()
        provider = get_provider(provider_id)
        brand_id = self._brand_for_provider(provider_id)
        self.provider_brand.set(brand_id)
        for item_id, button in self.provider_buttons.items():
            selected = item_id == brand_id
            button.config(
                text=("✓  " if selected else "") + next(
                    label for group_id, label, _ids in PROVIDER_GROUPS if group_id == item_id
                ),
                bg=CARD if selected else "#f1f3f7",
                fg=PRIMARY if selected else MUTED,
                activebackground=PRIMARY_SOFT if selected else "#e8ebf1",
                activeforeground=PRIMARY_DARK if selected else TEXT,
                font=("Microsoft YaHei UI", 9, "bold" if selected else "normal"),
            )
        if brand_id == "aliyun":
            self.plan_frame.grid()
        else:
            self.plan_frame.grid_remove()
        for item_id, button in self.plan_buttons.items():
            selected = item_id == provider_id
            button.config(
                bg=PRIMARY_SOFT if selected else "#f4f5f8",
                fg=PRIMARY_DARK if selected else MUTED,
                activebackground="#e3e1ff", activeforeground=PRIMARY_DARK,
                font=("Microsoft YaHei UI", 8, "bold" if selected else "normal"),
            )
        self.key_label.config(text=provider.key_label)
        model_id = self.model.get()
        if not any(item.id == model_id for item in provider.models):
            model_id = provider.default_model
            self.model.set(model_id)
        self._rebuild_model_rows(provider_id)
        if load_key:
            draft = self._key_drafts.get(provider_id)
            if draft is None:
                try:
                    draft = read_api_key(provider_id) or ""
                except CredentialError:
                    draft = ""
            self.key.set(draft)
        self._active_provider_id = provider_id
        profile = next(item for item in provider.models if item.id == self.model.get())
        key_state = "已保存 Key" if self.key.get().strip() else "尚未保存 Key"
        self.provider_info.config(
            text=f"✓  官方 Anthropic 兼容接口   ·   {key_state}   ·   {provider.short_label}",
            fg=SUCCESS if self.key.get().strip() else MUTED,
            bg="#f2faf6" if self.key.get().strip() else "#f5f6f9",
        )
        self.key_saved_label.config(
            text="✓ 已安全保存" if self.key.get().strip() else "仅保存在本机凭据管理器",
            fg=SUCCESS if self.key.get().strip() else MUTED,
        )
        self.detail.set(f"当前选择：{provider.label} · {profile.label}")

    def _remember_current_key(self) -> None:
        if self._active_provider_id:
            self._key_drafts[self._active_provider_id] = self.key.get()

    def _select_provider_brand(self, brand_id: str) -> None:
        self._remember_current_key()
        provider_ids = next(
            ids for item_id, _label, ids in PROVIDER_GROUPS if item_id == brand_id
        )
        current = self.provider.get()
        provider_id = current if current in provider_ids else provider_ids[0]
        self.provider.set(provider_id)
        self.model.set(PROVIDERS[provider_id].default_model)
        self._refresh_provider(load_key=True)

    def _select_aliyun_plan(self, provider_id: str) -> None:
        if provider_id not in ALIYUN_PLAN_LABELS:
            return
        self._remember_current_key()
        self.provider.set(provider_id)
        self.model.set(PROVIDERS[provider_id].default_model)
        self._refresh_provider(load_key=True)

    def _rebuild_model_rows(self, provider_id: str) -> None:
        for child in self.model_list.winfo_children():
            child.destroy()
        self.model_rows.clear()
        provider = get_provider(provider_id)
        for index, profile in enumerate(provider.models):
            row = tk.Frame(self.model_list, bg=SOFT, highlightbackground=LINE, highlightthickness=1)
            row.pack(fill="x", pady=(0, 5 if index < len(provider.models) - 1 else 0))
            accent = tk.Frame(row, bg=SOFT, width=4)
            accent.pack(side="left", fill="y")
            choice = tk.Button(
                row, text="", command=lambda selected=profile.id: self._select_model(selected),
                anchor="w", justify="left", relief="flat", bd=0, cursor="hand2",
                padx=14, pady=8, font=("Microsoft YaHei UI", 9),
            )
            choice.pack(side="left", fill="both", expand=True)
            tags = []
            if profile.id == provider.default_model:
                tags.append("推荐")
            if profile.context_tokens >= 1_000_000:
                tags.append("长上下文")
            tag = tk.Label(row, text="  ·  ".join(tags) or "均衡", bg=SOFT, fg=PRIMARY,
                           padx=12, font=("Microsoft YaHei UI", 8))
            tag.pack(side="right", fill="y")
            tag.bind("<Button-1>", lambda _event, selected=profile.id: self._select_model(selected))
            self.model_rows[profile.id] = (row, accent, choice)
        self._refresh_model_rows()

    def _select_model(self, model_id: str) -> None:
        self.model.set(model_id)
        self._refresh_model_rows()
        provider = get_provider(self.provider.get())
        profile = next(item for item in provider.models if item.id == model_id)
        self.detail.set(f"当前选择：{provider.label} · {profile.label}")

    def _refresh_model_rows(self) -> None:
        provider = get_provider(self.provider.get())
        selected = self.model.get()
        for profile in provider.models:
            row, accent, choice = self.model_rows[profile.id]
            active = profile.id == selected
            background = "#f3f2ff" if active else SOFT
            row.config(bg=background, highlightbackground=PRIMARY if active else LINE)
            accent.config(bg=PRIMARY if active else background)
            choice.config(
                text=f"{'●' if active else '○'}   {profile.label}\n      {profile.description}",
                bg=background, fg=TEXT if active else MUTED,
                activebackground=PRIMARY_SOFT if active else "#f0f2f6",
                activeforeground=TEXT,
                font=("Microsoft YaHei UI", 9, "bold" if active else "normal"),
            )
            for child in row.winfo_children():
                if isinstance(child, tk.Label):
                    child.config(bg=background)

    def _update_scroll_region(self, _event=None) -> None:
        self.content_canvas.configure(scrollregion=self.content_canvas.bbox("all"))

    def _sync_progress_ring(self, *_args) -> None:
        if hasattr(self, "progress_ring"):
            self.progress_ring.set(self.progress_value.get())

    def _resize_scroll_content(self, event) -> None:
        self.content_canvas.itemconfigure(self.content_window, width=event.width)

    def _enable_mousewheel(self, _event=None) -> None:
        self.bind_all("<MouseWheel>", self._on_mousewheel)

    def _disable_mousewheel(self, _event=None) -> None:
        self.unbind_all("<MouseWheel>")

    def _on_mousewheel(self, event) -> str:
        if event.widget is self.log:
            return ""
        delta = -1 if event.delta > 0 else 1
        self.content_canvas.yview_scroll(delta * 3, "units")
        return "break"

    def _navigate_sidebar(self, target: str) -> None:
        for key, button in self.sidebar_buttons.items():
            selected = key == target
            background = "#203f75" if selected else SIDEBAR
            button.config(
                bg=background,
                fg="white" if selected else "#aebdde",
                font=("Microsoft YaHei UI", 10, "bold" if selected else "normal"),
            )
            row, accent, badge = self.sidebar_rows[key]
            row.config(bg=background)
            accent.config(bg="#8175ff" if selected else SIDEBAR)
            badge.config(
                bg=background,
                image=self.step_badge_active if selected else self.step_badge_idle,
            )
        if target == "config":
            self.content_canvas.yview_moveto(0)
        elif target == "progress":
            self.update_idletasks()
            total = max(1, self.scroll_content.winfo_reqheight())
            self.content_canvas.yview_moveto(min(1.0, self.progress_card.winfo_y() / total))
        else:
            self.content_canvas.yview_moveto(1.0)

    def _open_provider_docs(self) -> None:
        webbrowser.open(get_provider(self.provider.get()).docs_url, new=2)

    def _toggle_key_button(self) -> None:
        self.show_key.set(not self.show_key.get())
        self._toggle_key_visibility()
        self.key_visibility_button.config(text="隐藏" if self.show_key.get() else "显示")

    def _toggle_log_height(self) -> None:
        expanded = int(self.log.cget("height")) > 6
        self.log.config(height=6 if expanded else 13)
        self.log_toggle.config(text="▤  查看详细日志  ⌄" if expanded else "▤  收起详细日志  ⌃")
        self.after_idle(self._update_scroll_region)

    def _action_button(
        self, parent: tk.Widget, text: str, command, icon: str, quiet: bool = False,
    ) -> IconButton:
        image = self.app_icon_small if icon == "claude" else self.ui_icons.get(icon)
        return IconButton(
            parent, text, command, icon, quiet=quiet, image=image,
            bordered=True,
        )

    def _hide_api_key(self) -> None:
        self.show_key.set(False)
        self.key_entry.config(show="●")
        self.key_visibility_button.config(text="显示")
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
        self.install_button.config(
            state="disabled" if busy else "normal",
            text="正在安装，请稍候…" if busy else "开始安装并配置",
        )
        action_state = "disabled" if busy else "normal"
        for button in (*self.provider_buttons.values(), *self.plan_buttons.values()):
            button.config(state=action_state)
        for _row, _accent, button in self.model_rows.values():
            button.config(state=action_state)
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
        self.task_badge.config(text="1", bg=SOFT, image=self.__dict__.get("step_badge_active"))
        self.task_title.config(text="准备开始")
        self.task_meta.config(text=f"共 {len(INSTALL_TASKS)} 个步骤")
        self.task_state.config(text="准备中", fg=PRIMARY)
        self.task_detail.config(text="正在准备自动安装流程…")
        self._refresh_milestones()

    def _complete_task(self, key: str) -> None:
        if key in dict(INSTALL_TASKS):
            self.completed_tasks.add(key)

    def _refresh_milestones(self) -> None:
        milestone_labels = self.__dict__.get("milestone_labels")
        if not milestone_labels:
            return
        for key, label in milestone_labels.items():
            if key in self.completed_tasks:
                label.config(text="✓  " + label.cget("text").split("  ", 1)[-1], fg=SUCCESS)
            elif key == self.active_task:
                label.config(text="◌  " + label.cget("text").split("  ", 1)[-1], fg=PRIMARY)
            else:
                label.config(text="○  " + label.cget("text").split("  ", 1)[-1], fg="#8992a4")

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
            self.task_badge.config(text="✓", bg=SOFT, image=self.__dict__.get("step_badge_success"))
            self.task_title.config(text="全部配置完成")
            self.task_meta.config(text=f"已完成 {len(INSTALL_TASKS)} 个步骤")
            self.task_state.config(text="100%", fg=SUCCESS)
            self.task_detail.config(text=detail)
            self._stop_progress_animation()
            self.progress_bar.config(mode="determinate")
            self.progress_value.set(100)
            self.progress_text.config(text="100%")
            self._refresh_milestones()
            return
        task_map = dict(INSTALL_TASKS)
        if key not in task_map:
            return
        if self.active_task and self.active_task != key:
            self._complete_task(self.active_task)
        self.active_task = key
        task_index = next(i for i, item in enumerate(INSTALL_TASKS) if item[0] == key)
        self.current_task_bar.stop()
        self.task_badge.config(
            text=str(task_index + 1), bg=SOFT,
            image=self.__dict__.get("step_badge_active"),
        )
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
        self._refresh_milestones()

    def _set_indeterminate(self, text: str) -> None:
        if not self.progress_animating:
            self.progress_bar.config(mode="indeterminate")
            self.progress_bar.start(12)
            self.progress_animating = True
        self.progress_text.config(text=text)
        if hasattr(self, "progress_ring"):
            self.progress_ring.set(None, "…")

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
        provider_id = self.provider.get()
        provider = get_provider(provider_id)
        api_key = self.key.get().strip()
        if not api_key:
            messagebox.showwarning("需要 API Key", f"请先粘贴 {provider.key_label}。")
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
        self._worker(lambda: install_all(
            self.paths, api_key, model, self._progress, options, provider_id,
        ))

    def _test(self) -> None:
        provider_id = self.provider.get()
        provider = get_provider(provider_id)
        api_key = self.key.get().strip()
        self._hide_api_key()
        if api_key:
            try:
                write_api_key(api_key, provider_id)
                self._refresh_provider(load_key=False)
            except Exception as exc:
                messagebox.showerror("无法保存", str(exc))
                return
        def work():
            self.events.put(("progress", f"连接测试\n正在连接 {provider.label}…"))
            ok, message = test_connection(self.model.get(), provider_id)
            self.events.put(("success" if ok else "error", message))
        self._worker(work)

    def _launch(self, launcher) -> None:
        def work():
            self.events.put(("progress", "启动中\n正在注入当前模型厂商的直连环境…"))
            launcher(self.paths)
            self.events.put(("success", "已启动。请在新窗口中开始使用。"))
        self._worker(work)

    def _show_status(self) -> None:
        def work():
            status = system_status(self.paths)
            message = (
                f"连接：{status['provider_label']} Anthropic 直连\n"
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
                    self.task_badge.config(
                        text="✓", bg=SOFT,
                        image=self.__dict__.get("step_badge_success"),
                    )
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
                    self.progress_ring.set(None, "!")
                    self.current_task_bar.stop()
                    self.current_task_bar.config(mode="determinate")
                    self.task_badge.config(
                        text="!", bg=SOFT,
                        image=self.__dict__.get("step_badge_error"),
                    )
                    self.task_state.config(text="需要处理", fg=ERROR)
                    self.task_detail.config(text=payload)
                    self.status.set("未完成")
                    self.detail.set(payload)
                    messagebox.showerror("遇到问题", payload)
                elif kind == "install-options" and isinstance(payload, InstallSourceError):
                    self._stop_progress_animation()
                    self.progress_bar.config(mode="determinate")
                    self.progress_text.config(text="等待选择")
                    self.progress_ring.set(None, "…")
                    self.task_badge.config(
                        text="!", bg=SOFT,
                        image=self.__dict__.get("step_badge_error"),
                    )
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
