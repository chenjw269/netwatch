from __future__ import annotations

import ctypes
from ctypes import wintypes
import threading
import time
import tkinter as tk
import tkinter.font as tkfont
import traceback

from netwatch.config import (
    DEEP_PING_COUNT,
    INTERNET_HOST,
    INTERVAL_CHOICES,
    interval_label,
    normalize_interval,
)
from netwatch.diagnose import diagnose
from netwatch.models import Diagnosis, PingSample, ProxyState, SiteProbe, Snapshot, WifiInfo
from netwatch.store import (
    autostart_command,
    autostart_enabled,
    current_autostart_command,
    load_settings,
    log_error,
    save_settings,
    set_autostart,
)
from netwatch.system import collect, ping_host

FONT = "Microsoft YaHei UI"
# 展开后面板的目标宽度，按 96 DPI 下的像素。实际宽度会乘上系统缩放。
EXPANDED_WIDTH = 520
WRAP = 420
PANEL_WRAP = 460
FONT_TITLE = (FONT, 17, "bold")
FONT_LATENCY = (FONT, 24, "bold")
FONT_TEXT = (FONT, 14)
FONT_MUTED = (FONT, 13)
FONT_SECTION = (FONT, 12)
CARD = "#141820"
PANEL = "#f5f7fb"
PANEL_FG = "#1b2330"
PANEL_MUTED = "#5e6878"
LINE = "#313949"
FG = "#f4f6fb"
MUTED = "#8e96a8"
OK = "#3dbe86"
WARN = "#e0a84a"
BAD = "#ef6a73"
IDLE = "#8b9bff"
BUTTON = "#2a3142"
BUTTON_ACTIVE = "#3a445c"
BUTTON_DISABLED = "#1c212c"
ACCENT = "#3d4f92"
ACCENT_FG = "#f4f6ff"
WARN_FILL = "#5c4630"
WARN_FG = "#ffe7c4"
CORNER = 26
PANEL_CORNER = 18

_LEVELS = {"ok": OK, "warn": WARN, "bad": BAD, "idle": IDLE}
_MUTEX = None


class App:
    def __init__(self) -> None:
        self.settings = load_settings()
        self.expanded = bool(self.settings.get("expanded", False))
        self.mini = bool(self.settings.get("mini", False))
        self.history: list[int | None] = []
        self._stop = threading.Event()
        self._wake = threading.Event()
        self._paused = threading.Event()
        self._dragged = False
        self._ignore_click = False
        self._press_at = (0, 0)
        self._origin = (0, 0)
        self._diagnosis = Diagnosis("idle", "正在检测", "先看电脑到路由器，再看到国内互联网。")
        self._snapshot = Snapshot()
        self._deep_running = False
        self._scroll_offset = 0
        self._content_height = 1
        self._view_height = 1
        self._menu: tk.Toplevel | None = None
        self._toast: tk.Toplevel | None = None
        self._toast_after: str | None = None

        self.root = tk.Tk()
        self.root.title("网络波动")
        self.root.configure(bg=CARD)
        self.root.overrideredirect(True)
        _apply_scaling(self.root)

        self.paused_var = tk.BooleanVar(value=bool(self.settings.get("paused", False)))
        self.autostart_var = tk.BooleanVar(value=autostart_enabled())
        self.topmost_var = tk.BooleanVar(value=bool(self.settings.get("topmost", True)))
        self._interval = normalize_interval(self.settings.get("interval_sec"))
        if self.settings.get("interval_sec") != self._interval:
            self.settings["interval_sec"] = self._interval
            self._persist()
        self._apply_topmost()
        if self.paused_var.get():
            self._paused.set()

        self._build()
        self.root.withdraw()
        self._place(first=True)
        self.root.deiconify()
        self._apply_topmost()
        self.root.after(200, self._raise)
        self.root.bind_all("<ButtonPress-1>", self._press)
        self.root.bind_all("<B1-Motion>", self._motion)
        self.root.bind_all("<ButtonRelease-1>", self._release)
        self.root.bind_all("<Button-3>", self._popup)
        self.root.protocol("WM_DELETE_WINDOW", self.close)

        if not self.settings.get("autostart_initialized"):
            try:
                set_autostart(True)
                self.autostart_var.set(True)
            except OSError:
                log_error(traceback.format_exc())
            self.settings["autostart_initialized"] = True
            self._persist()
        elif autostart_enabled() and current_autostart_command() != autostart_command():
            try:
                set_autostart(True)
            except OSError:
                log_error(traceback.format_exc())

        self._worker = threading.Thread(target=self._loop, name="netwatch", daemon=True)
        self._worker.start()

    def run(self) -> None:
        self.root.mainloop()

    def close(self) -> None:
        self._stop.set()
        self._wake.set()
        self.root.destroy()

    def _build(self) -> None:
        self.shell = tk.Frame(self.root, bg=CARD)
        self.shell.pack(fill="both", expand=True)
        self.dock_rail = tk.Frame(self.shell, bg=CARD)
        self.dock_rail.pack(side="left", fill="y", padx=(12, 2))
        self.dock = _Dock(self.dock_rail)
        self.dock.pack(expand=True)
        self.dock.bind("<ButtonRelease-1>", lambda _event: self._toggle_mini())
        self.body = tk.Frame(self.shell, bg=CARD, padx=16, pady=14)
        self.body.pack(side="left", fill="both", expand=True)

        self.header = tk.Frame(self.body, bg=CARD)
        self.header.pack(fill="x")
        self.dot = tk.Canvas(self.header, width=22, height=22, bg=CARD, highlightthickness=0)
        self.dot.pack(side="left", pady=2)
        self._glow_id = self.dot.create_oval(1, 1, 21, 21, outline=_mix(CARD, IDLE, 0.55), width=1)
        self._ring_id = self.dot.create_oval(4, 4, 18, 18, outline=IDLE, width=2)
        self._dot_id = self.dot.create_oval(8, 8, 14, 14, fill=IDLE, outline="")
        self.title_var = tk.StringVar(value="正在检测")
        self._title_font = tkfont.Font(font=FONT_TITLE)
        self.title_label = tk.Label(
            self.header,
            textvariable=self.title_var,
            fg=FG,
            bg=CARD,
            font=FONT_TITLE,
            anchor="w",
            justify="left",
            wraplength=240,
        )
        self.title_label.pack(side="left", padx=(8, 8))
        self.chevron = self._button(self.header, "详情", self.toggle, side="right")
        self.chevron.pack_forget()
        self.latency_var = tk.StringVar(value="…")
        self.latency = _Readout(self.header)

        self.sub_var = tk.StringVar(value="先看电脑到路由器，再看到 223.5.5.5")
        self.sub_label = tk.Label(
            self.body,
            textvariable=self.sub_var,
            fg=MUTED,
            bg=CARD,
            font=FONT_MUTED,
            anchor="w",
            justify="left",
            wraplength=WRAP,
        )

        self.divider = tk.Frame(self.body, bg=LINE, height=1)
        self.divider.pack_propagate(False)

        self._detail_text_width = 420
        self.detail_host = tk.Frame(self.body, bg=PANEL)
        self.detail_view = tk.Frame(self.detail_host, bg=PANEL, height=1)
        self.detail_view.pack_propagate(False)
        self.detail_view.pack(side="left", fill="both", expand=True, padx=(12, 4), pady=12)
        self.details = tk.Frame(self.detail_view, bg=PANEL)
        self.details.place(x=0, y=0, width=PANEL_WRAP)
        self.detail_scroll = tk.Scrollbar(
            self.detail_host,
            orient="vertical",
            command=self._on_scrollbar,
            bg="#6d7586",
            troughcolor="#d5dbe6",
            activebackground="#9aa3b5",
            width=12,
            bd=0,
            highlightthickness=0,
            relief="flat",
        )
        self.root.bind_all("<MouseWheel>", self._wheel)

        self._section(self.details, "判断")
        self.rule_var = tk.StringVar(value="")
        tk.Label(
            self.details,
            textvariable=self.rule_var,
            fg=PANEL_MUTED,
            bg=PANEL,
            font=FONT_MUTED,
            justify="left",
            anchor="w",
            wraplength=PANEL_WRAP,
        ).pack(fill="x", pady=(0, 4))
        self.detail_var = tk.StringVar(value="")
        tk.Label(
            self.details,
            textvariable=self.detail_var,
            fg=PANEL_FG,
            bg=PANEL,
            font=FONT_TEXT,
            justify="left",
            anchor="w",
            wraplength=PANEL_WRAP,
        ).pack(fill="x")

        self._section(self.details, "分段")
        self.gw_title = tk.StringVar(value="电脑 → 路由器")
        self.gw_stat = tk.StringVar(value="等待")
        self._stat_block(self.details, self.gw_title, self.gw_stat)
        self.inet_title = tk.StringVar(value=f"路由器 → 互联网    {INTERNET_HOST}")
        self.inet_stat = tk.StringVar(value="等待")
        self._stat_block(self.details, self.inet_title, self.inet_stat)
        tk.Label(
            self.details,
            text="柱子越高越慢，红格是这一轮超时。",
            fg=PANEL_MUTED,
            bg=PANEL,
            font=FONT_MUTED,
            justify="left",
            anchor="w",
            wraplength=PANEL_WRAP,
        ).pack(fill="x", pady=(2, 0))
        self.spark = tk.Canvas(self.details, width=PANEL_WRAP, height=42, bg=PANEL, highlightthickness=0)
        self.spark.pack(fill="x", pady=(6, 0))
        self.deep_var = tk.StringVar(value="")
        self.deep_label = tk.Label(
            self.details, textvariable=self.deep_var, fg=PANEL_MUTED, bg=PANEL, font=FONT_MUTED, anchor="w"
        )

        self._section(self.details, "无线")
        self.wifi_var = tk.StringVar(value="正在读取")
        self.wifi_sub = tk.StringVar(value="")
        tk.Label(
            self.details,
            textvariable=self.wifi_var,
            fg=PANEL_FG,
            bg=PANEL,
            font=FONT_TEXT,
            anchor="w",
            wraplength=PANEL_WRAP,
            justify="left",
        ).pack(fill="x")
        tk.Label(
            self.details,
            textvariable=self.wifi_sub,
            fg=PANEL_MUTED,
            bg=PANEL,
            font=FONT_MUTED,
            anchor="w",
            wraplength=PANEL_WRAP,
            justify="left",
        ).pack(fill="x")
        self.wifi_extra = tk.Frame(self.details, bg=PANEL)
        self.wifi_extra.pack(fill="x")
        self.wifi_hint = tk.StringVar(value="")
        self.wifi_hint_label = tk.Label(
            self.wifi_extra,
            textvariable=self.wifi_hint,
            fg=PANEL_MUTED,
            bg=PANEL,
            font=FONT_MUTED,
            justify="left",
            anchor="w",
            wraplength=PANEL_WRAP,
        )

        self._section(self.details, "拨测")
        self.proxy_var = tk.StringVar(value="")
        tk.Label(
            self.details,
            textvariable=self.proxy_var,
            fg=PANEL_MUTED,
            bg=PANEL,
            font=FONT_MUTED,
            anchor="w",
            wraplength=PANEL_WRAP,
            justify="left",
        ).pack(fill="x")
        self.site_box = tk.Frame(self.details, bg=PANEL)
        self.site_box.pack(fill="x", pady=(2, 0))

        self.actions = tk.Frame(self.body, bg=CARD)
        rule = tk.Frame(self.actions, bg=LINE, height=1)
        rule.pack(fill="x", pady=(2, 10))
        rule.pack_propagate(False)
        interval = tk.Frame(self.actions, bg=CARD)
        interval.pack(fill="x", pady=(0, 8))
        tk.Label(interval, text="监测间隔", fg=MUTED, bg=CARD, font=FONT_MUTED).pack(side="left", padx=(0, 8))
        self._interval_buttons: dict[int, _Pill] = {}
        for seconds in INTERVAL_CHOICES:
            self._interval_buttons[seconds] = self._button(
                interval,
                interval_label(seconds),
                lambda chosen=seconds: self._set_interval(chosen),
                gap=6,
            )
        tools = tk.Frame(self.actions, bg=CARD)
        tools.pack(fill="x")
        self.autostart_button = self._button(tools, "开机启动", self._toggle_autostart)
        self.pause_button = self._button(tools, "暂停", self._toggle_pause, kind="warn")
        self.topmost_button = self._button(tools, "顶层显示：是", self._toggle_topmost)
        self._button(tools, "退出", self.close, role="ghost")
        corner = tk.Frame(self.actions, bg=CARD)
        corner.pack(fill="x", pady=(8, 0))
        self.deep_button = self._button(corner, "丢包率测试", self.deep_loss)
        self._button(corner, "复制诊断", self.copy_diagnosis)
        self._sync_buttons()
        self._layout()

    def _section(self, parent: tk.Frame, text: str) -> None:
        row = tk.Frame(parent, bg=PANEL)
        row.pack(fill="x", pady=(14, 4))
        tick = tk.Canvas(row, width=3, height=12, bg=PANEL, highlightthickness=0, bd=0)
        tick.pack(side="left", padx=(0, 8), pady=1)
        tick.create_rectangle(0, 0, 3, 12, fill=IDLE, outline="")
        tk.Label(row, text=text, fg=PANEL_MUTED, bg=PANEL, font=FONT_SECTION).pack(side="left")

    def _stat_block(self, parent: tk.Frame, title: tk.StringVar, stat: tk.StringVar) -> None:
        tk.Label(parent, textvariable=title, fg=PANEL_FG, bg=PANEL, font=(FONT, 14, "bold"), anchor="w").pack(fill="x")
        tk.Label(parent, textvariable=stat, fg=PANEL_MUTED, bg=PANEL, font=FONT_MUTED, anchor="w").pack(
            fill="x", pady=(2, 8)
        )

    def _button(
        self,
        parent: tk.Frame,
        text: str,
        command,
        side: str = "left",
        role: str = "solid",
        kind: str = "accent",
        gap: int = 8,
    ) -> "_Pill":
        return _Pill(parent, text, command, side, role, kind, gap)

    def _loop(self) -> None:
        while not self._stop.is_set():
            if self._paused.is_set():
                self._wake.wait(0.3)
                self._wake.clear()
                continue
            started = time.monotonic()
            try:
                snapshot = collect()
                found = diagnose(snapshot)
            except Exception:
                log_error(traceback.format_exc())
                snapshot = Snapshot(error="这一轮检测失败，下一轮会再试。", collected_at=time.time())
                found = diagnose(snapshot)
            try:
                self.root.after(0, self._apply, snapshot, found)
            except tk.TclError:
                return
            self._sleep_until(started)

    def kick(self) -> None:
        if self._paused.is_set():
            return
        self._wake.set()

    def _sleep_until(self, started: float) -> None:
        while not self._stop.is_set():
            remaining = started + self._interval - time.monotonic()
            if remaining <= 0:
                return
            if self._wake.wait(min(remaining, 1.0)):
                self._wake.clear()
                return

    def _set_interval(self, seconds: int) -> None:
        chosen = normalize_interval(seconds)
        if chosen == self._interval:
            self._sync_buttons()
            return
        self._interval = chosen
        self.settings["interval_sec"] = chosen
        self._sync_buttons()
        self._persist()

    def _cycle_interval(self) -> None:
        try:
            index = INTERVAL_CHOICES.index(self._interval)
        except ValueError:
            index = -1
        self._set_interval(INTERVAL_CHOICES[(index + 1) % len(INTERVAL_CHOICES)])

    def _apply(self, snapshot: Snapshot, found: Diagnosis) -> None:
        try:
            self._snapshot = snapshot
            self._diagnosis = found
            color = _LEVELS.get(found.level, IDLE)
            self.dot.itemconfigure(self._dot_id, fill=color)
            self.dot.itemconfigure(self._ring_id, outline=color)
            self.dot.itemconfigure(self._glow_id, outline=_mix(CARD, color, 0.55))
            self.latency.configure(fg=color)
            self.title_var.set("已暂停" if self._paused.is_set() else found.title)
            self.latency_var.set(_latency_text(snapshot))
            self._sync_latency_style()
            self.sub_var.set(_subline(snapshot))
            self.gw_title.set(f"电脑 → 路由器    {snapshot.lan_gateway or '—'}")
            self.gw_stat.set(_ping_text(snapshot.gateway_ping))
            self.inet_stat.set(_ping_text(snapshot.internet_ping))
            self._fill_wifi(snapshot.wifi, found)
            self.proxy_var.set(_proxy_text(snapshot.proxy))
            self._fill_sites(snapshot)
            self.rule_var.set(f"命中规则：{found.rule}" if found.rule else "")
            self.detail_var.set(found.detail)
            self._push_history(snapshot)
            self._draw_history()
            self._fit_title()
            if self.mini:
                self.root.update_idletasks()
            self._fit_details()
            needed_h = max(self.shell.winfo_reqheight(), 52)
            needed_w = self._window_width()
            if abs(needed_h - self.root.winfo_height()) > 2 or abs(needed_w - self.root.winfo_width()) > 2:
                self._place(first=False)
        except tk.TclError:
            return

    def _fill_wifi(self, wifi: WifiInfo | None, found: Diagnosis) -> None:
        show_hint = False
        if wifi is None or not wifi.connected:
            self.wifi_var.set("没有已连接的无线")
            self.wifi_sub.set("如果用的是网线，分段诊断仍然有效。")
            if self.wifi_hint_label.winfo_manager():
                self.wifi_hint_label.pack_forget()
            return
        self.wifi_var.set(wifi.ssid or "无线已连接")
        bits = [
            item
            for item in (
                wifi.band,
                f"信道 {wifi.channel}" if wifi.channel else "",
                wifi.width,
                wifi.radio,
            )
            if item
        ]
        signal = []
        if wifi.signal is not None:
            signal.append(f"信号 {wifi.signal}%")
        if wifi.rssi is not None:
            signal.append(f"RSSI {wifi.rssi}")
        if wifi.rx_mbps is not None:
            signal.append(f"速率 {wifi.rx_mbps:g} Mbps")
        self.wifi_sub.set("\n".join(part for part in (" · ".join(bits), " · ".join(signal)) if part))
        if wifi.band and "2.4" in wifi.band and found.title != "疑似 2.4GHz 干扰":
            strong = (wifi.signal is not None and wifi.signal >= 70) or (
                wifi.rssi is not None and wifi.rssi >= -65
            )
            if strong:
                hint = "信号本身不弱。2.4 GHz 容易被隔壁网络干扰，突然变慢或连续超时时，网卡仍会显示已连接。"
            else:
                hint = "当前在 2.4 GHz。信号如果继续变差，延迟会先抖。"
            if self.wifi_hint.get() != hint:
                self.wifi_hint.set(hint)
            show_hint = True
            if not self.wifi_hint_label.winfo_manager():
                self.wifi_hint_label.pack(fill="x", pady=(4, 0))
        if not show_hint and self.wifi_hint_label.winfo_manager():
            self.wifi_hint_label.pack_forget()

    def _fill_sites(self, snapshot: Snapshot) -> None:
        rows = self._site_rows(snapshot)
        signature = tuple((name, address) for name, _text, _color, address in rows)
        if signature != getattr(self, "_site_signature", None):
            for child in self.site_box.winfo_children():
                child.destroy()
            self._site_signature = signature
            self._site_values = []
            if not rows:
                tk.Label(self.site_box, text="等待这一轮拨测", fg=PANEL_MUTED, bg=PANEL, font=FONT_MUTED).pack(anchor="w")
                return
            for name, text, color, address in rows:
                row = tk.Frame(self.site_box, bg=PANEL)
                row.pack(fill="x", pady=(2, 1))
                top = tk.Frame(row, bg=PANEL)
                top.pack(fill="x")
                tk.Label(top, text=name, fg=PANEL_FG, bg=PANEL, font=FONT_TEXT).pack(side="left")
                value = tk.StringVar(value=text)
                value_label = tk.Label(top, textvariable=value, fg=color, bg=PANEL, font=FONT_TEXT)
                value_label.pack(side="right")
                address_var = None
                if address:
                    address_var = tk.StringVar(value=address)
                    tk.Label(
                        row,
                        textvariable=address_var,
                        fg=PANEL_MUTED,
                        bg=PANEL,
                        font=FONT_MUTED,
                        anchor="w",
                        justify="left",
                        wraplength=self._detail_text_width,
                    ).pack(fill="x")
                self._site_values.append((value, value_label, address_var))
            return
        for (value, value_label, address_var), (_name, text, color, address) in zip(self._site_values, rows):
            if value.get() != text:
                value.set(text)
            if value_label.cget("fg") != color:
                value_label.configure(fg=color)
            if address_var is not None and address and address_var.get() != address:
                address_var.set(address)

    def _site_rows(self, snapshot: Snapshot) -> list[tuple[str, str, str, str]]:
        rows = []
        for site in snapshot.sites:
            if site.ok and site.ms is not None:
                text, color = f"{site.ms} ms", OK if site.ms < 400 else WARN
            else:
                text, color = site.error or "失败", BAD
            rows.append((site.name, text, color, _site_address(site)))
        return rows

    def _push_history(self, snapshot: Snapshot) -> None:
        sample = snapshot.internet_ping
        if sample.sent <= 0 and not snapshot.collected_at:
            return
        point = sample.avg_ms if sample.replied else None
        self.history.append(point)
        del self.history[:-30]

    def _draw_history(self) -> None:
        canvas = self.spark
        data = tuple(self.history[-28:])
        if data == getattr(self, "_drawn_history", None):
            return
        self._drawn_history = data
        canvas.delete("all")
        width = int(canvas["width"])
        height = int(canvas["height"])
        slots = 28
        gap = width / slots
        canvas.create_line(2, height - 2, width - 2, height - 2, fill="#d5dbe6")
        if not data:
            return
        values = [item for item in data if item is not None]
        peak = max(values + [50])
        start = slots - len(data)
        for index, value in enumerate(data):
            x0 = (start + index) * gap + 2
            x1 = x0 + max(gap - 4, 2)
            if value is None:
                _round_bar(canvas, x0, 2, x1, height - 1, BAD)
                continue
            bar = max(4, int(value / peak * (height - 4)))
            color = OK if value < 80 else WARN if value < 150 else BAD
            _round_bar(canvas, x0, height - bar, x1, height - 1, color)

    def _layout(self) -> None:
        self.sub_label.pack_forget()
        self.chevron.pack_forget()
        self.latency.pack_forget()
        self.title_label.pack_forget()
        self.divider.pack_forget()
        self.detail_host.pack_forget()
        self.actions.pack_forget()
        if self.mini:
            self.dock.configure(text="展开", padx=14, pady=4)
            self.dock_rail.pack_configure(padx=(10, 8), pady=10)
            self.body.configure(padx=12, pady=8)
            self.latency.pack(side="right", padx=(8, 14))
            self.title_label.pack(side="left", padx=(8, 8))
            self._sync_latency_style()
            self._fit_title()
            return
        self.dock.configure(text="收\n起", padx=10, pady=10)
        self.dock_rail.pack_configure(padx=(12, 2), pady=0)
        self.body.configure(padx=16, pady=14)
        self.chevron.pack(side="right", padx=(12, 2))
        self.latency.pack(side="right", padx=(8, 8))
        self.title_label.pack(side="left", padx=(8, 8), fill="x", expand=True)
        self.sub_label.pack(fill="x", pady=(8, 0))
        if self.expanded:
            self.divider.pack(fill="x", pady=(10, 8))
            self.detail_host.pack(fill="x")
            self.actions.pack(fill="x", pady=(8, 0))
            self.chevron.configure(text="收起")
            self._fit_details()
        else:
            self.chevron.configure(text="详情")
        self._sync_latency_style()
        self._fit_title()

    def _sync_latency_style(self) -> None:
        text = self.latency_var.get()
        # 毫秒数字保持大号；「超时」「…」和结论用同一字号。
        font = FONT_LATENCY if text[:1].isdigit() else FONT_TITLE
        self._latency_font = font
        self.latency.configure(text=text, font=font)

    def _fit_title(self) -> None:
        natural = max(int(self._title_font.measure(self.title_var.get())), 48)
        scale = _ui_scale(self.root)
        if self.mini or not self.expanded:
            # 收起时按文字本身排成一行，窗口跟着变窄，不再留出一大段空白。
            width = min(natural, int(460 * scale))
        else:
            if not self.title_label.winfo_ismapped():
                return
            header = self.header.winfo_width()
            if header < 80:
                return
            used = 16
            for widget, gap in ((self.dot, 8), (self.latency, 20), (self.chevron, 16)):
                if widget.winfo_ismapped():
                    used += max(widget.winfo_reqwidth(), 1) + gap
            width = max(int(160 * scale), header - used)
        current = int(float(self.title_label.cget("wraplength")))
        if abs(current - width) > 2:
            self.title_label.configure(wraplength=width)

    def _toggle_mini(self) -> None:
        self._set_mini(not self.mini)

    def _set_mini(self, mini: bool) -> None:
        self.mini = mini
        self.settings["mini"] = mini
        self._persist()
        self._layout()
        self._place(first=False)

    def toggle(self) -> None:
        if self.mini:
            self._set_mini(False)
            return
        self.expanded = not self.expanded
        self.settings["expanded"] = self.expanded
        self._persist()
        self._layout()
        self._place(first=False)

    def _toggle_pause(self) -> None:
        paused = not self._paused.is_set()
        if paused:
            self._paused.set()
        else:
            self._paused.clear()
            self._wake.set()
        self.paused_var.set(paused)
        self.settings["paused"] = paused
        self._sync_buttons()
        self._persist()
        self.title_var.set("已暂停" if paused else self._diagnosis.title)

    def _toggle_autostart(self) -> None:
        enabled = not autostart_enabled()
        try:
            set_autostart(enabled)
        except OSError:
            log_error(traceback.format_exc())
            enabled = autostart_enabled()
        self.autostart_var.set(enabled)
        self._sync_buttons()

    def _toggle_topmost(self) -> None:
        self.topmost_var.set(not self.topmost_var.get())
        self.settings["topmost"] = self.topmost_var.get()
        self._apply_topmost()
        self._sync_buttons()
        self._persist()

    def _apply_topmost(self) -> None:
        on = self.topmost_var.get()
        if getattr(self, "_topmost_on", None) == on and self._menu is None:
            return
        try:
            self.root.attributes("-topmost", on)
            if on and getattr(self, "_topmost_on", None) is not on:
                self.root.lift()
            menu = self._menu
            if menu is not None:
                menu.attributes("-topmost", True)
                menu.lift()
            self._topmost_on = on
        except tk.TclError:
            return

    def _sync_buttons(self) -> None:
        self.autostart_button.configure(
            text="开机启动：开" if self.autostart_var.get() else "开机启动：关",
            selected=self.autostart_var.get(),
        )
        self.pause_button.configure(text="继续" if self.paused_var.get() else "暂停", selected=self.paused_var.get())
        self.topmost_button.configure(
            text="顶层显示：是" if self.topmost_var.get() else "顶层显示：否",
            selected=self.topmost_var.get(),
        )
        for seconds, button in self._interval_buttons.items():
            button.configure(selected=seconds == self._interval)

    def deep_loss(self) -> None:
        if self._deep_running:
            return
        if self.mini:
            self._set_mini(False)
        if not self.expanded:
            self.toggle()
        self._deep_running = True
        self.deep_button.configure(state="disabled", text="正在测试…")
        self.deep_var.set("正在测丢包率，请稍等。")
        self.deep_label.pack(fill="x", pady=(4, 0))
        self._place(first=False)

        def work() -> None:
            sample = ping_host(INTERNET_HOST, DEEP_PING_COUNT)
            self.root.after(0, self._show_deep, sample)

        threading.Thread(target=work, name="netwatch-loss", daemon=True).start()

    def _show_deep(self, sample: PingSample) -> None:
        self._deep_running = False
        try:
            self.deep_button.configure(state="normal", text="丢包率测试")
            if sample.sent == 0:
                self.deep_var.set(sample.error or "丢包率测试失败")
            else:
                avg = f"，平均 {sample.avg_ms} ms" if sample.avg_ms is not None else ""
                longest = f"，最长 {sample.max_ms} ms" if sample.max_ms is not None else ""
                self.deep_var.set(f"丢包率 {sample.loss_pct}%{avg}{longest}")
            self._place(first=False)
        except tk.TclError:
            return

    def copy_diagnosis(self) -> None:
        text = self._report()
        self.root.clipboard_clear()
        self.root.clipboard_append(text)
        self.root.update()
        self._show_copied()

    def _show_copied(self) -> None:
        self._hide_copied()
        tip = tk.Toplevel(self.root)
        tip.overrideredirect(True)
        tip.configure(bg="#222633")
        tk.Label(tip, text="已复制", fg=FG, bg="#222633", font=FONT_TEXT, padx=22, pady=12).pack()
        tip.withdraw()
        tip.update_idletasks()
        width = tip.winfo_reqwidth()
        height = tip.winfo_reqheight()
        x = self.root.winfo_rootx() + max(0, (self.root.winfo_width() - width) // 2)
        y = self.root.winfo_rooty() + max(0, (self.root.winfo_height() - height) // 2)
        tip.geometry(f"{width}x{height}+{x}+{y}")
        try:
            tip.attributes("-topmost", True)
        except tk.TclError:
            tip.destroy()
            return
        tip.deiconify()
        tip.lift()
        _round_window(tip, max(tip.winfo_height() // 2, 12))
        self._toast = tip
        self._toast_after = self.root.after(1200, self._hide_copied)

    def _hide_copied(self) -> None:
        if self._toast_after is not None:
            try:
                self.root.after_cancel(self._toast_after)
            except tk.TclError:
                pass
            self._toast_after = None
        tip = self._toast
        self._toast = None
        if tip is not None:
            try:
                tip.destroy()
            except tk.TclError:
                return

    def _report(self) -> str:
        snapshot = self._snapshot
        lines = [
            self._diagnosis.title,
            self._diagnosis.detail,
            _ping_text(snapshot.gateway_ping),
            _ping_text(snapshot.internet_ping),
        ]
        if snapshot.wifi and snapshot.wifi.connected:
            lines.append(self.wifi_sub.get())
        for site in snapshot.sites:
            address = _site_address(site)
            prefix = f"{site.name} {address}".strip()
            if site.ok and site.ms is not None:
                lines.append(f"{prefix} {site.ms} ms")
            else:
                lines.append(f"{prefix} {site.error or '失败'}")
        return "\n".join(line for line in lines if line)

    def _popup(self, event: tk.Event) -> None:
        if self._menu is not None and _widget_in(event.widget, self._menu):
            return
        self._close_menu()
        menu = tk.Toplevel(self.root)
        menu.overrideredirect(True)
        menu.configure(bg="#222633")
        self._menu = menu
        entries: list[tuple[str, object] | None] = [
            ("收成小窗" if not self.mini else "恢复窗口", self._toggle_mini),
            ("收起" if self.expanded else "展开详情", self.toggle),
            ("立即检测", self.kick),
            ("继续监测" if self._paused.is_set() else "暂停监测", self._toggle_pause),
            (f"间隔：{interval_label(self._interval)}", self._cycle_interval),
            ("开机启动：开" if autostart_enabled() else "开机启动：关", self._toggle_autostart),
            ("顶层显示：是" if self.topmost_var.get() else "顶层显示：否", self._toggle_topmost),
            ("丢包率测试", self.deep_loss),
            ("复制诊断", self.copy_diagnosis),
            None,
            ("退出", self.close),
        ]
        for entry in entries:
            if entry is None:
                tk.Frame(menu, bg="#3a4258", height=1).pack(fill="x", padx=10, pady=4)
                continue
            label, command = entry
            row = tk.Label(
                menu,
                text=label,
                bg="#222633",
                fg=FG,
                font=FONT_MUTED,
                anchor="w",
                padx=16,
                pady=6,
                cursor="hand2",
            )
            row._menu_command = command  # type: ignore[attr-defined]
            row.pack(fill="x")
            row.bind("<Enter>", lambda _event, widget=row: widget.configure(bg=BUTTON_ACTIVE))
            row.bind("<Leave>", lambda _event, widget=row: widget.configure(bg="#222633"))
        menu.withdraw()
        menu.update_idletasks()
        width = max(menu.winfo_reqwidth(), 160)
        height = menu.winfo_reqheight()
        screen_w = menu.winfo_screenwidth()
        screen_h = menu.winfo_screenheight()
        x = min(max(0, event.x_root), max(0, screen_w - width))
        y = min(max(0, event.y_root), max(0, screen_h - height))
        menu.geometry(f"{width}x{height}+{x}+{y}")
        try:
            menu.attributes("-topmost", True)
        except tk.TclError:
            self._close_menu()
            return
        menu.deiconify()
        menu.lift()
        _round_window(menu, 12)

    def _close_menu(self) -> None:
        menu = self._menu
        self._menu = None
        if menu is None:
            return
        try:
            menu.destroy()
        except tk.TclError:
            return

    def _press(self, event: tk.Event) -> None:
        if self._menu is not None:
            self._ignore_click = True
            if _widget_in(event.widget, self._menu):
                command = getattr(event.widget, "_menu_command", None)
                if command is not None:
                    self._close_menu()
                    command()
                return
            self._close_menu()
            return
        if getattr(event.widget, "_is_pill", False) or getattr(event.widget, "_is_dock", False) or isinstance(
            event.widget, (tk.Button, tk.Scrollbar)
        ):
            self._ignore_click = True
            return
        self._ignore_click = False
        self._press_at = (event.x_root, event.y_root)
        self._origin = (self.root.winfo_x(), self.root.winfo_y())
        self._dragged = False

    def _motion(self, event: tk.Event) -> None:
        if self._ignore_click:
            return
        dx = event.x_root - self._press_at[0]
        dy = event.y_root - self._press_at[1]
        if abs(dx) > 3 or abs(dy) > 3:
            self._dragged = True
        if self._dragged:
            self.root.geometry(f"+{self._origin[0] + dx}+{self._origin[1] + dy}")

    def _release(self, event: tk.Event) -> None:
        if self._ignore_click:
            return
        if self._dragged:
            self._save_position()
            return
        if self.mini:
            self._set_mini(False)
            return
        if not _widget_in(event.widget, self.detail_host):
            self.toggle()

    def _save_position(self) -> None:
        self.settings["x"] = self.root.winfo_x()
        self.settings["y"] = self.root.winfo_y()
        self._persist()

    def _persist(self) -> None:
        try:
            save_settings(self.settings)
        except OSError:
            log_error(traceback.format_exc())

    def _detail_inner_width(self, reserve_scroll: bool) -> int:
        host = self.detail_host.winfo_width()
        scale = _ui_scale(self.root)
        if host < 80:
            host = max(int(300 * scale), self._window_width() - int(140 * scale))
        used = 16
        if reserve_scroll:
            used += 28
        return max(int(180 * scale), host - used)

    def _window_width(self) -> int:
        """收起时贴着内容；展开时用同一套物理宽度，并限制在屏幕以内。"""
        scale = _ui_scale(self.root)
        screen_w = max(int(self.root.winfo_screenwidth()), 800)
        content = max(int(self.shell.winfo_reqwidth()), int(180 * scale))
        cap = int(screen_w * 0.72)
        if self.mini or not self.expanded:
            return min(content, cap)
        design = int(round(EXPANDED_WIDTH * scale))
        return min(max(content, design), cap)

    def _apply_detail_width(self, width: int) -> None:
        if width == self._detail_text_width and getattr(self, "_detail_width_ready", False):
            return
        self._detail_width_ready = True
        self._detail_text_width = width
        self.spark.configure(width=width)

        def walk(widget: tk.Misc) -> None:
            for child in widget.winfo_children():
                if isinstance(child, tk.Label):
                    child.configure(wraplength=width)
                walk(child)

        walk(self.details)

    def _fit_details(self) -> None:
        if not self.expanded or self.mini:
            return
        self.detail_host.update_idletasks()
        self._apply_detail_width(self._detail_inner_width(False))
        self.details.update_idletasks()
        needed = max(self.details.winfo_reqheight(), 1)
        cap = max(420, int(self.root.winfo_screenheight() * 0.48))
        view = min(needed, cap)
        use_scroll = needed > view + 4
        if use_scroll:
            self._apply_detail_width(self._detail_inner_width(True))
            self.details.update_idletasks()
            needed = max(self.details.winfo_reqheight(), 1)
            view = min(needed, cap)
            if not self.detail_scroll.winfo_ismapped():
                self.detail_scroll.pack(side="right", fill="y", padx=(0, 8), pady=12)
        else:
            self.detail_scroll.pack_forget()
        self._content_height = needed
        self._view_height = view
        if abs(self.detail_view.winfo_height() - view) > 2:
            self.detail_view.configure(height=view)
        self._move_details(self._scroll_offset if use_scroll else 0)

    def _max_offset(self) -> int:
        return max(0, self._content_height - self._view_height)

    def _move_details(self, offset: int) -> None:
        offset = max(0, min(int(offset), self._max_offset()))
        if offset != self._scroll_offset or getattr(self, "_placed_detail_width", None) != self._detail_text_width:
            self._scroll_offset = offset
            self._placed_detail_width = self._detail_text_width
            self.details.place(x=0, y=-offset, width=self._detail_text_width)
        needed = max(self._content_height, 1)
        if self._max_offset() <= 0:
            self.detail_scroll.set(0, 1)
            return
        first = offset / needed
        last = min(1.0, (offset + self._view_height) / needed)
        self.detail_scroll.set(first, last)

    def _on_scrollbar(self, *args: str) -> None:
        if not self.expanded or self._max_offset() <= 0:
            return
        if args[0] == "moveto":
            self._move_details(float(args[1]) * self._content_height)
            return
        if args[0] != "scroll":
            return
        step = int(args[1])
        amount = step * self._view_height if len(args) > 2 and args[2] == "pages" else step * 36
        self._move_details(self._scroll_offset + amount)

    def _wheel(self, event: tk.Event) -> None:
        if not self.expanded or not _widget_in(event.widget, self.detail_host):
            return
        if self._max_offset() <= 0:
            return
        notches = int(getattr(event, "delta", 0) / 120) or 0
        if notches == 0:
            return
        self._move_details(self._scroll_offset - notches * 48)

    def _place(self, first: bool) -> None:
        if self.mini:
            self._sync_latency_style()
            self._fit_title()
        self.root.update_idletasks()
        width = self._window_width()
        height = max(self.shell.winfo_reqheight(), 52)
        screen_w = self.root.winfo_screenwidth()
        screen_h = self.root.winfo_screenheight()
        if first:
            x = self.settings.get("x")
            y = self.settings.get("y")
            if not isinstance(x, int) or not isinstance(y, int):
                x = screen_w - width - 24
                y = screen_h - height - 96
        else:
            x = self.root.winfo_x()
            y = self.root.winfo_y()
        if x < 0 or x > screen_w - 48 or x + width > screen_w - 8:
            x = max(8, screen_w - width - 24)
        if y < 0 or y + height > screen_h - 4:
            y = max(8, screen_h - height - 48)
        same = (
            abs(self.root.winfo_width() - width) <= 2
            and abs(self.root.winfo_height() - height) <= 2
            and abs(self.root.winfo_x() - x) <= 1
            and abs(self.root.winfo_y() - y) <= 1
        )
        if not same:
            self.root.geometry(f"{width}x{height}+{x}+{y}")
            self.root.update_idletasks()
        if not self.mini:
            self._fit_title()
        if self.expanded and not self.mini:
            self._fit_details()
            self.root.update_idletasks()
            height = max(self.shell.winfo_reqheight(), 52)
            if y + height > screen_h - 4:
                y = max(8, screen_h - height - 48)
            self.root.geometry(f"{width}x{height}+{x}+{y}")
            self.root.update_idletasks()
        _round_window(self.root, CORNER)
        if self.expanded and not self.mini:
            self._mask_panel_corners()

    def _raise(self) -> None:
        self._apply_topmost()

    def _mask_panel_corners(self) -> None:
        radius = PANEL_CORNER
        if not hasattr(self, "_corner_masks"):
            self._corner_masks = {}
            for key, relx, rely, anchor in (
                ("nw", 0, 0, "nw"),
                ("ne", 1, 0, "ne"),
                ("sw", 0, 1, "sw"),
                ("se", 1, 1, "se"),
            ):
                canvas = tk.Canvas(
                    self.detail_host,
                    width=radius,
                    height=radius,
                    bg=PANEL,
                    highlightthickness=0,
                    bd=0,
                )
                canvas.place(relx=relx, rely=rely, anchor=anchor)
                self._corner_masks[key] = canvas
        boxes = {
            "nw": (0, 0, radius * 2, radius * 2),
            "ne": (-radius, 0, radius, radius * 2),
            "sw": (0, -radius, radius * 2, radius),
            "se": (-radius, -radius, radius, radius),
        }
        for key, canvas in self._corner_masks.items():
            canvas.configure(width=radius, height=radius)
            canvas.delete("all")
            canvas.create_rectangle(0, 0, radius, radius, fill=CARD, outline="")
            canvas.create_oval(*boxes[key], fill=PANEL, outline="")
            tk.Misc.lift(canvas)


def _site_address(site: SiteProbe) -> str:
    if site.ip:
        return f"{site.host}   {site.ip}".strip()
    return site.host


def _latency_text(snapshot: Snapshot) -> str:
    sample = snapshot.internet_ping
    if sample.sent <= 0:
        return "…"
    if not sample.replied or sample.avg_ms is None:
        return "超时"
    return f"{sample.avg_ms} ms"


def _subline(snapshot: Snapshot) -> str:
    if not snapshot.collected_at:
        return "先看电脑到路由器，再看到 223.5.5.5"
    clock = time.strftime("%H:%M:%S", time.localtime(snapshot.collected_at))
    return (
        f"{_short_hop('电脑→路由器', snapshot.gateway_ping)}\n"
        f"{_short_hop('路由器→互联网', snapshot.internet_ping)}\n"
        f"上次检测 {clock}"
    )


def _short_hop(label: str, sample: PingSample) -> str:
    if sample.sent <= 0:
        return f"{label} —"
    if not sample.replied or sample.avg_ms is None:
        return f"{label} 超时"
    loss = f" 丢{sample.loss_pct}%" if sample.loss_pct else ""
    return f"{label} {sample.avg_ms}ms{loss}"


def _ping_text(sample: PingSample) -> str:
    if sample.sent <= 0:
        return sample.error or "没有数据"
    if not sample.replied:
        return f"连续超时 · 丢包 {sample.loss_pct}%"
    avg = f"{sample.avg_ms} ms" if sample.avg_ms is not None else "—"
    longest = f"{sample.max_ms} ms" if sample.max_ms is not None else "—"
    return f"平均 {avg}    最长 {longest}    丢包 {sample.loss_pct}%"


def _proxy_text(proxy: ProxyState) -> str:
    if proxy.tun_likely:
        return "VPN 接管了默认路由，国内和国外都测"
    if proxy.system_proxy_on:
        return (
            f"当前系统代理 {proxy.server}\n"
            "国内网站直接连接\n"
            "国外网站通过代理连接"
        )
    if proxy.clash_running:
        return "Clash 在运行，系统代理没开，只测国内"
    return "未开代理，只测国内网站"


class _Dock(tk.Canvas):
    """左侧收起按钮。圆角，避免一块方标签贴在窗口边上。"""

    def __init__(self, parent: tk.Misc) -> None:
        super().__init__(parent, bg=CARD, highlightthickness=0, bd=0, cursor="hand2")
        self._is_dock = True
        self._text = "收\n起"
        self._hot = False
        self._font = tkfont.Font(font=FONT_MUTED)
        self._redraw()
        self.bind("<Enter>", self._enter)
        self.bind("<Leave>", self._leave)

    def configure(self, cnf=None, **kwargs):  # type: ignore[override]
        if cnf:
            kwargs.update(cnf)
        text = kwargs.pop("text", None)
        kwargs.pop("padx", None)
        kwargs.pop("pady", None)
        kwargs.pop("bg", None)
        if text is not None and text != self._text:
            self._text = text
            self._redraw()
        if kwargs:
            super().configure(**kwargs)

    def _enter(self, _event: tk.Event) -> None:
        self._hot = True
        self._redraw()

    def _leave(self, _event: tk.Event) -> None:
        self._hot = False
        self._redraw()

    def _redraw(self) -> None:
        lines = self._text.split("\n")
        line_h = max(self._font.metrics("linespace"), 16)
        text_w = max(self._font.measure(line) for line in lines)
        width = text_w + 22
        height = line_h * len(lines) + 18
        super().configure(width=width, height=height)
        self.delete("all")
        _paint_round(self, 1, 1, width - 1, height - 1, min(16, height // 2), fill=BUTTON_ACTIVE if self._hot else BUTTON)
        top = (height - line_h * len(lines)) / 2
        for index, line in enumerate(lines):
            self.create_text(width / 2, top + line_h * index + line_h / 2, text=line, fill=FG, font=self._font)


class _Readout(tk.Canvas):
    """延迟数字。带一层状态色底，不像直接贴在深色背景上。"""

    def __init__(self, parent: tk.Misc) -> None:
        super().__init__(parent, bg=CARD, highlightthickness=0, bd=0)
        self._text = "…"
        self._fg = IDLE
        self._font = tkfont.Font(font=FONT_LATENCY)
        self._redraw()

    def configure(self, cnf=None, **kwargs):  # type: ignore[override]
        if cnf:
            kwargs.update(cnf)
        text = kwargs.pop("text", None)
        fg = kwargs.pop("fg", None)
        font = kwargs.pop("font", None)
        changed = False
        if text is not None and text != self._text:
            self._text = text
            changed = True
        if fg is not None and fg != self._fg:
            self._fg = fg
            changed = True
        if font is not None:
            family, size = font[0], font[1]
            weight = font[2] if len(font) > 2 else "normal"
            if (self._font.cget("family"), int(self._font.cget("size")), self._font.cget("weight")) != (
                family,
                int(size),
                weight,
            ):
                self._font.configure(family=family, size=size, weight=weight)
                changed = True
        if changed:
            self._redraw()
        if kwargs:
            super().configure(**kwargs)

    def _redraw(self) -> None:
        pad_x = 12
        pad_y = 5
        width = max(self._font.measure(self._text) + pad_x * 2, 52)
        height = max(self._font.metrics("linespace") + pad_y * 2, 28)
        super().configure(width=width, height=height)
        self.delete("all")
        _paint_round(self, 1, 1, width - 1, height - 1, height // 2, fill=_mix(CARD, self._fg, 0.2))
        self.create_text(width / 2, height / 2, text=self._text, fill=self._fg, font=self._font)


class _Pill(tk.Canvas):
    """底部操作用的圆角按钮。选中时填上强调色。"""

    def __init__(
        self,
        parent: tk.Misc,
        text: str,
        command,
        side: str,
        role: str = "solid",
        kind: str = "accent",
        gap: int = 8,
    ) -> None:
        super().__init__(parent, bg=CARD, highlightthickness=0, bd=0, cursor="hand2")
        self._is_pill = True
        self._command = command
        self._label = text
        self._enabled = True
        self._hot = False
        self._selected = False
        self._role = role
        self._kind = kind
        self._font = tkfont.Font(font=FONT_MUTED)
        self._redraw()
        self.bind("<Enter>", self._enter)
        self.bind("<Leave>", self._leave)
        self.bind("<ButtonRelease-1>", self._activate)
        pad = (0, gap) if side == "left" else (gap, 4)
        self.pack(side=side, padx=pad)

    def configure(self, cnf=None, **kwargs):  # type: ignore[override]
        if cnf:
            kwargs.update(cnf)
        text = kwargs.pop("text", None)
        state = kwargs.pop("state", None)
        selected = kwargs.pop("selected", None)
        changed = False
        if text is not None and text != self._label:
            self._label = text
            changed = True
        if state is not None:
            enabled = state != "disabled"
            if enabled != self._enabled:
                self._enabled = enabled
                super().configure(cursor="hand2" if self._enabled else "arrow")
                changed = True
        if selected is not None and bool(selected) != self._selected:
            self._selected = bool(selected)
            changed = True
        if changed:
            self._redraw()
        if kwargs:
            super().configure(**kwargs)

    def _enter(self, _event: tk.Event) -> None:
        self._hot = True
        self._redraw()

    def _leave(self, _event: tk.Event) -> None:
        self._hot = False
        self._redraw()

    def _activate(self, _event: tk.Event) -> None:
        if self._enabled and self._hot:
            self._command()

    def _colors(self) -> tuple[str, str]:
        if not self._enabled:
            return BUTTON_DISABLED, MUTED
        if self._selected:
            if self._kind == "warn":
                return (_mix(WARN_FILL, "#ffffff", 0.08) if self._hot else WARN_FILL), WARN_FG
            return (_mix(ACCENT, "#ffffff", 0.1) if self._hot else ACCENT), ACCENT_FG
        if self._role == "ghost":
            return (BUTTON_ACTIVE if self._hot else "#1c2230"), (FG if self._hot else MUTED)
        if self._hot:
            return BUTTON_ACTIVE, FG
        return BUTTON, FG

    def _redraw(self) -> None:
        linespace = max(self._font.metrics("linespace"), 16)
        pad_y = max(8, linespace // 4)
        width = self._font.measure(self._label) + linespace + 10
        height = linespace + pad_y * 2
        super().configure(width=width, height=height)
        self.delete("all")
        fill, fg = self._colors()
        radius = height // 2
        if self._role == "ghost" and not self._selected:
            _paint_round(self, 1, 1, width - 1, height - 1, radius, fill=BUTTON)
            _paint_round(self, 2, 2, width - 2, height - 2, radius - 1, fill=fill)
        else:
            _paint_round(self, 1, 1, width - 1, height - 1, radius, fill=fill)
        self.create_text(width / 2, height / 2, text=self._label, fill=fg, font=self._font)


def _mix(base: str, tint: str, amount: float) -> str:
    def channel(color: str, index: int) -> int:
        return int(color[index : index + 2], 16)

    parts = []
    for index in (1, 3, 5):
        mixed = channel(base, index) * (1 - amount) + channel(tint, index) * amount
        parts.append(f"{max(0, min(255, round(mixed))):02x}")
    return "#" + "".join(parts)


def _round_bar(canvas: tk.Canvas, x0: float, y0: float, x1: float, y1: float, fill: str) -> None:
    if y1 - y0 < 3 or x1 - x0 < 2:
        canvas.create_rectangle(x0, y0, x1, y1, fill=fill, outline="")
        return
    radius = min(3, (x1 - x0) / 2, (y1 - y0) / 2)
    canvas.create_rectangle(x0, y0 + radius, x1, y1, fill=fill, outline="")
    canvas.create_oval(x0, y0, x1, y0 + radius * 2, fill=fill, outline="")


def _paint_round(canvas: tk.Canvas, x1: int, y1: int, x2: int, y2: int, radius: int, fill: str) -> None:
    radius = min(radius, (x2 - x1) // 2, (y2 - y1) // 2)
    canvas.create_polygon(
        x1 + radius,
        y1,
        x2 - radius,
        y1,
        x2,
        y1,
        x2,
        y1 + radius,
        x2,
        y2 - radius,
        x2,
        y2,
        x2 - radius,
        y2,
        x1 + radius,
        y2,
        x1,
        y2,
        x1,
        y2 - radius,
        x1,
        y1 + radius,
        x1,
        y1,
        smooth=True,
        fill=fill,
        outline="",
    )


def _round_window(widget: tk.Misc, radius: int) -> None:
    try:
        user = ctypes.windll.user32
        gdi = ctypes.windll.gdi32
        user.GetAncestor.argtypes = [wintypes.HWND, ctypes.c_uint]
        user.GetAncestor.restype = wintypes.HWND
        user.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
        user.SetWindowRgn.argtypes = [wintypes.HWND, wintypes.HRGN, wintypes.BOOL]
        user.SetWindowRgn.restype = wintypes.BOOL
        gdi.CreateRoundRectRgn.restype = wintypes.HRGN
        hwnd = user.GetAncestor(wintypes.HWND(int(widget.winfo_id())), 2)
        if not hwnd:
            hwnd = wintypes.HWND(int(widget.winfo_id()))
        rect = wintypes.RECT()
        if not user.GetWindowRect(hwnd, ctypes.byref(rect)):
            return
        width = int(rect.right - rect.left)
        height = int(rect.bottom - rect.top)
        if width < 8 or height < 8:
            return
        key = (int(hwnd), width, height, radius)
        if getattr(widget, "_region_key", None) == key:
            return
        region = gdi.CreateRoundRectRgn(0, 0, width + 1, height + 1, radius * 2, radius * 2)
        if not region:
            return
        if user.SetWindowRgn(hwnd, region, True):
            widget._region_key = key
    except Exception:
        return


def _widget_in(widget: tk.Misc, parent: tk.Misc) -> bool:
    current = widget
    while current is not None:
        if current is parent:
            return True
        current = getattr(current, "master", None)
    return False


def _ui_scale(root: tk.Misc) -> float:
    """1 表示 96 DPI。字号已经跟着系统缩放，窗口像素也按这个倍数计算。"""
    try:
        scaling = float(root.tk.call("tk", "scaling"))
    except (tk.TclError, TypeError, ValueError):
        scaling = 96 / 72
    return max(0.85, scaling / (96 / 72))


def _apply_scaling(root: tk.Tk) -> None:
    try:
        hdc = ctypes.windll.user32.GetDC(0)
        dpi = ctypes.windll.gdi32.GetDeviceCaps(hdc, 88)
        ctypes.windll.user32.ReleaseDC(0, hdc)
    except Exception:
        return
    if not dpi:
        return
    expected = dpi / 72.0
    try:
        current = float(root.tk.call("tk", "scaling"))
    except tk.TclError:
        return
    if current < expected * 0.8:
        root.tk.call("tk", "scaling", expected)


def _enable_dpi() -> None:
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            return


def _single_instance() -> bool:
    global _MUTEX
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    _MUTEX = kernel32.CreateMutexW(None, False, "Local\\NetWatchFloatingWidget")
    return ctypes.get_last_error() != 183


def _lower_priority() -> None:
    try:
        kernel32 = ctypes.windll.kernel32
        kernel32.SetPriorityClass(kernel32.GetCurrentProcess(), 0x00004000)
    except Exception:
        return


def main() -> None:
    _enable_dpi()
    if not _single_instance():
        return
    _lower_priority()
    try:
        App().run()
    except Exception:
        log_error(traceback.format_exc())
        raise
