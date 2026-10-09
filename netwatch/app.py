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
    PING_WAIT_MS,
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
CARD = "#242b3a"
EDGE = "#4a5670"
PANEL = "#f5f7fb"
SHEET = "#ffffff"
SECTION_HEAD = "#e8edf6"
SECTION_EDGE = "#d5dced"
INSET = "#f3f6fb"
PANEL_FG = "#1b2330"
PANEL_MUTED = "#5e6878"
LINE = "#4a5670"
FG = "#f4f6fb"
MUTED = "#8e96a8"
OK = "#3dbe86"
WARN = "#e0a84a"
BAD = "#ef6a73"
IDLE = "#8b9bff"
BUTTON = "#343d52"
BUTTON_ACTIVE = "#45506c"
BUTTON_DISABLED = "#2a3142"
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
        self.root.configure(bg=EDGE)
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
        self.shell.pack(fill="both", expand=True, padx=1, pady=1)
        self.body = tk.Frame(self.shell, bg=CARD, padx=16, pady=14)
        self.body.pack(fill="both", expand=True)

        self.header = tk.Frame(self.body, bg=CARD)
        self.header.pack(fill="x")
        # 还原后：左边是按钮，右边是状态和链路说明，说明文字互相左对齐。
        self.summary = tk.Frame(self.body, bg=CARD)
        self.button_col = tk.Frame(self.summary, bg=CARD)
        self.rest = tk.Frame(self.summary, bg=CARD)
        self.text_col = tk.Frame(self.summary, bg=CARD)
        self.status_row = tk.Frame(self.text_col, bg=CARD)
        self.dock = _Dock(self.body)
        self.dock.bind("<ButtonRelease-1>", lambda _event: self._toggle_mini())
        self.dot = tk.Canvas(self.body, width=22, height=22, bg=CARD, highlightthickness=0)
        self._glow_id = self.dot.create_oval(1, 1, 21, 21, outline=_mix(CARD, IDLE, 0.55), width=1)
        self._ring_id = self.dot.create_oval(4, 4, 18, 18, outline=IDLE, width=2)
        self._dot_id = self.dot.create_oval(8, 8, 14, 14, fill=IDLE, outline="")
        self.title_var = tk.StringVar(value="正在检测")
        self._title_font = tkfont.Font(font=FONT_TITLE)
        self.title_label = tk.Label(
            self.body,
            textvariable=self.title_var,
            fg=FG,
            bg=CARD,
            font=FONT_TITLE,
            anchor="w",
            justify="left",
            wraplength=240,
        )
        self.chevron = self._button(self.body, "展开详情", self.toggle, side="right")
        self.chevron.pack_forget()
        self.latency_var = tk.StringVar(value="…")
        self.latency = _Readout(self.body)

        self.sub_var = tk.StringVar(value="先看电脑到路由器，再看到 223.5.5.5")
        self.sub_label = tk.Label(
            self.body,
            textvariable=self.sub_var,
            fg=MUTED,
            bg=CARD,
            font=FONT_MUTED,
            anchor="nw",
            justify="left",
            wraplength=WRAP,
        )

        self.divider = tk.Frame(self.body, bg=LINE, height=1)
        self.divider.pack_propagate(False)

        self._detail_text_width = 420
        self.detail_host = tk.Frame(self.body, bg=PANEL)
        self.detail_view = tk.Frame(self.detail_host, bg=PANEL, height=1)
        self.detail_view.pack_propagate(False)
        self.detail_view.pack(
            side="left",
            fill="both",
            expand=True,
            padx=(PANEL_CORNER, PANEL_CORNER),
            pady=(16, 12),
        )
        self.details = tk.Frame(self.detail_view, bg=PANEL)
        self.details.place(x=0, y=0, width=PANEL_WRAP)
        self.detail_scroll = _DetailBar(self.detail_host, self._on_scrollbar)
        self.root.bind_all("<MouseWheel>", self._wheel)

        judge = self._section(self.details, "诊断结果", IDLE, first=True)
        self.rule_var = tk.StringVar(value="")
        tk.Label(
            judge,
            textvariable=self.rule_var,
            fg=PANEL_MUTED,
            bg=SHEET,
            font=FONT_MUTED,
            justify="left",
            anchor="w",
            wraplength=PANEL_WRAP,
        ).pack(fill="x", pady=(0, 4))
        self.detail_box = tk.Text(
            judge,
            fg=PANEL_FG,
            bg=SHEET,
            font=FONT_TEXT,
            wrap="char",
            relief="flat",
            borderwidth=0,
            highlightthickness=0,
            padx=0,
            pady=0,
            height=1,
            width=20,
            cursor="ibeam",
            insertwidth=1,
            selectbackground="#d5e2fb",
            selectforeground=PANEL_FG,
            inactiveselectbackground="#d5e2fb",
        )
        self.detail_box.pack(fill="x", anchor="w")
        self.detail_box._selectable = True  # type: ignore[attr-defined]
        self.detail_box.bind("<Key>", self._readonly_key)
        self.detail_box.bind("<Control-a>", self._select_detail)
        self.detail_box.bind("<Control-A>", self._select_detail)
        self.detail_box.bind("<Control-v>", lambda _event: "break")
        self.detail_box.bind("<Control-V>", lambda _event: "break")
        self.detail_box.bind("<Control-x>", lambda _event: "break")
        self.detail_box.bind("<Control-X>", lambda _event: "break")
        self.detail_box.bind("<<Paste>>", lambda _event: "break")
        self.detail_box.bind("<<Cut>>", lambda _event: "break")
        self.detail_box.bind("<Button-2>", lambda _event: "break")

        hops = self._section(self.details, "链路测试", OK)
        self.gw_title = tk.StringVar(value="电脑 → 路由器")
        self.gw_stat = tk.StringVar(value="等待")
        self._stat_block(hops, self.gw_title, self.gw_stat)
        self.inet_title = tk.StringVar(value=f"路由器 → 互联网    {INTERNET_HOST}")
        self.inet_stat = tk.StringVar(value="等待")
        self._stat_block(hops, self.inet_title, self.inet_stat)
        tk.Label(
            hops,
            text="柱子的高度代表时延，\n红色代表该轮连接测试超时。",
            fg=PANEL_MUTED,
            bg=SHEET,
            font=FONT_MUTED,
            justify="left",
            anchor="w",
            wraplength=PANEL_WRAP,
        ).pack(fill="x", pady=(2, 0))
        self.spark = tk.Canvas(hops, width=PANEL_WRAP, height=42, bg=SHEET, highlightthickness=0)
        self.spark.pack(fill="x", pady=(6, 0))
        self.deep_var = tk.StringVar(value="")
        self.deep_label = tk.Label(
            hops, textvariable=self.deep_var, fg=PANEL_MUTED, bg=SHEET, font=FONT_MUTED, anchor="w"
        )

        wireless = self._section(self.details, "Wifi 信息", "#5b8def")
        self.wifi_var = tk.StringVar(value="正在读取")
        self.wifi_sub = tk.StringVar(value="")
        tk.Label(
            wireless,
            textvariable=self.wifi_var,
            fg=PANEL_FG,
            bg=SHEET,
            font=(FONT, 14, "bold"),
            anchor="w",
            wraplength=PANEL_WRAP,
            justify="left",
        ).pack(fill="x")
        tk.Label(
            wireless,
            textvariable=self.wifi_sub,
            fg=PANEL_MUTED,
            bg=SHEET,
            font=FONT_MUTED,
            anchor="w",
            wraplength=PANEL_WRAP,
            justify="left",
        ).pack(fill="x", pady=(2, 0))
        self.wifi_extra = tk.Frame(wireless, bg=SHEET)
        self.wifi_extra.pack(fill="x")
        self.wifi_hint = tk.StringVar(value="")
        self.wifi_hint_label = tk.Label(
            self.wifi_extra,
            textvariable=self.wifi_hint,
            fg=PANEL_MUTED,
            bg=SHEET,
            font=FONT_MUTED,
            justify="left",
            anchor="w",
            wraplength=PANEL_WRAP,
        )

        probe = self._section(self.details, "连接测试", WARN)
        self.proxy_var = tk.StringVar(value="")
        tk.Label(
            probe,
            textvariable=self.proxy_var,
            fg=PANEL_MUTED,
            bg=SHEET,
            font=FONT_MUTED,
            anchor="w",
            wraplength=PANEL_WRAP,
            justify="left",
        ).pack(fill="x")
        self.site_box = tk.Frame(probe, bg=SHEET)
        self.site_box.pack(fill="x", pady=(6, 0))

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
        self._button(corner, "复制诊断结果", self.copy_diagnosis)
        self._sync_buttons()
        self._layout()

    def _section(self, parent: tk.Frame, text: str, accent: str, first: bool = False) -> tk.Frame:
        outer = tk.Frame(parent, bg=PANEL)
        outer.pack(fill="x", pady=(0 if first else 16, 0))
        shell = tk.Frame(outer, bg=SECTION_EDGE)
        shell.pack(fill="x")
        card = tk.Frame(shell, bg=SHEET)
        card.pack(fill="x", padx=1, pady=1)
        head = tk.Frame(card, bg=SECTION_HEAD)
        head.pack(fill="x")
        mark = tk.Frame(head, bg=accent, width=4)
        mark.pack(side="left", fill="y")
        mark.pack_propagate(False)
        tk.Label(head, text=text, fg=PANEL_FG, bg=SECTION_HEAD, font=(FONT, 13, "bold")).pack(
            side="left", padx=(10, 8), pady=8
        )
        body = tk.Frame(card, bg=SHEET)
        body.pack(fill="x", padx=10, pady=(8, 10))
        return body

    def _stat_block(self, parent: tk.Frame, title: tk.StringVar, stat: tk.StringVar) -> None:
        box = tk.Frame(parent, bg=INSET)
        box.pack(fill="x", pady=(0, 8))
        tk.Label(box, textvariable=title, fg=PANEL_FG, bg=INSET, font=(FONT, 13, "bold"), anchor="w").pack(
            fill="x", padx=8, pady=(6, 0)
        )
        tk.Label(box, textvariable=stat, fg=PANEL_MUTED, bg=INSET, font=FONT_MUTED, anchor="w").pack(
            fill="x", padx=8, pady=(2, 6)
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
            paused = self._paused.is_set()
            self.title_var.set("已暂停" if paused else found.title)
            self.title_label.configure(fg=MUTED if paused else color)
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
            self._set_detail_text(found.detail)
            self._push_history(snapshot)
            self._draw_history()
            self._fit_title()
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
            self.wifi_sub.set("如果用的是网线，链路测试仍然有效。")
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
        if wifi.band and "2.4" in wifi.band and found.title != "电脑→路由器 2.4GHz 干扰":
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
                tk.Label(self.site_box, text="等待这一轮连接测试", fg=PANEL_MUTED, bg=SHEET, font=FONT_MUTED).pack(anchor="w")
                return
            for name, text, color, address in rows:
                row = tk.Frame(self.site_box, bg=SHEET)
                row.pack(fill="x", pady=(3, 2))
                top = tk.Frame(row, bg=SHEET)
                top.pack(fill="x")
                dot = tk.Canvas(top, width=8, height=8, bg=SHEET, highlightthickness=0, bd=0)
                dot.pack(side="left", padx=(0, 6), pady=5)
                mark = dot.create_oval(0, 0, 8, 8, fill=color, outline="")
                tk.Label(top, text=name, fg=PANEL_FG, bg=SHEET, font=FONT_TEXT).pack(side="left")
                value = tk.StringVar(value=text)
                value_label = tk.Label(top, textvariable=value, fg=color, bg=SHEET, font=FONT_TEXT, anchor="e")
                value_label.pack(side="right", padx=(8, 4))
                address_var = None
                if address:
                    address_var = tk.StringVar(value=address)
                    tk.Label(
                        row,
                        textvariable=address_var,
                        fg=PANEL_MUTED,
                        bg=SHEET,
                        font=FONT_MUTED,
                        anchor="w",
                        justify="left",
                        wraplength=self._copy_width(),
                    ).pack(fill="x", padx=(14, 0))
                self._site_values.append((value, value_label, address_var, dot, mark))
            return
        for (value, value_label, address_var, dot, mark), (_name, text, color, address) in zip(self._site_values, rows):
            if value.get() != text:
                value.set(text)
            if value_label.cget("fg") != color:
                value_label.configure(fg=color)
            dot.itemconfigure(mark, fill=color)
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
        floor = height - 3
        canvas.create_line(2, height - 2, width - 2, height - 2, fill="#d5dbe6")
        if not data:
            return
        values = [item for item in data if item is not None]
        peak = max(values + [50])
        start = slots - len(data)
        usable = max(floor - 2, 6)
        for index, value in enumerate(data):
            span = min(max(gap - 6, 4), 8)
            x0 = (start + index) * gap + (gap - span) / 2
            x1 = x0 + span
            if value is None:
                _round_bar(canvas, x0, 2, x1, floor, BAD)
                continue
            bar = max(6, int(value / peak * usable))
            color = OK if value < 80 else WARN if value < 150 else BAD
            _round_bar(canvas, x0, floor - bar, x1, floor, color)

    def _layout_all(self) -> None:
        self.sub_label.pack_forget()
        self.chevron.pack_forget()
        self.dock.pack_forget()
        self.summary.pack_forget()
        self.status_row.pack_forget()
        self.header.pack_forget()
        self.dot.pack_forget()
        self.latency.pack_forget()
        self.title_label.pack_forget()
        self.divider.pack_forget()
        self.detail_host.pack_forget()
        self.actions.pack_forget()
        self.dock.set_span(None)
        self.chevron.set_span(None)
        self._summary_ready = False
        if self.mini:
            self.dock.configure(text="还原")
            self.body.configure(padx=8, pady=8)
            self.header.pack(fill="x")
            self.dock.pack(in_=self.header, side="left", anchor="center", padx=(0, 6))
            self.dot.pack(in_=self.header, side="left", anchor="center")
            self.latency.pack(in_=self.header, side="right", anchor="center", padx=(4, 0))
            self.title_label.pack(in_=self.header, side="left", anchor="center", padx=(6, 2))
            self._sync_latency_style()
            self._fit_title()
            return
        self.dock.configure(text="小窗显示")
        self.chevron.configure(text="收起详情" if self.expanded else "展开详情")
        span = self._paired_span()
        self.dock.set_span(span)
        self.chevron.set_span(span)
        # 收起和展开用同一套边距。这里一变，上面的说明就会整块挪一下。
        self.body.configure(padx=16, pady=12)
        self.summary.pack(fill="x")
        self.button_col.pack(side="left", anchor="n", padx=(0, 12))
        self.rest.pack(side="left", fill="x", expand=True)
        self.text_col.grid_forget()
        # 说明贴在按钮右侧，展开后多余宽度留在右边，不把文字推到中间。
        self.text_col.pack(in_=self.rest, side="left", anchor="nw")
        self.dock.pack(in_=self.button_col, side="top", anchor="w")
        self.chevron.pack(in_=self.button_col, side="top", anchor="w", pady=(6, 0))
        self.status_row.pack(anchor="w")
        self.dot.pack(in_=self.status_row, side="left", anchor="center")
        self.title_label.pack(in_=self.status_row, side="left", anchor="center", padx=(6, 2))
        self.latency.pack(in_=self.status_row, side="left", anchor="center", padx=(4, 0))
        # 链路说明和绿色圆点左对齐。展开后整块落在按钮右侧空白的中间。
        self.sub_label.pack(in_=self.text_col, anchor="w", pady=(8, 0))
        self._summary_ready = True
        if self.expanded:
            self.divider.pack(fill="x", pady=(10, 8))
            self.detail_host.pack(fill="x")
            self.actions.pack(fill="x", pady=(8, 0))
            self._fit_details()
        self._sync_latency_style()
        self._fit_title()

    def _layout(self) -> None:
        # 展开/收起时按钮和说明还在，只切换详情，避免整页拆掉再装回来。
        if not self.mini and getattr(self, "_summary_ready", False) and self.summary.winfo_ismapped():
            self._sync_expanded()
            return
        self._layout_all()

    def _sync_expanded(self) -> None:
        self.chevron.configure(text="收起详情" if self.expanded else "展开详情")
        span = self._paired_span()
        self.dock.set_span(span)
        self.chevron.set_span(span)
        self.divider.pack_forget()
        self.detail_host.pack_forget()
        self.actions.pack_forget()
        if self.expanded:
            self.divider.pack(fill="x", pady=(10, 8))
            self.detail_host.pack(fill="x")
            self.actions.pack(fill="x", pady=(8, 0))
            self._fit_details()
        self._sync_latency_style()

    def _paired_span(self) -> int:
        linespace = max(self.chevron._font.metrics("linespace"), 16)
        dock_w = max(self.dock._font.measure(label) for label in ("小窗显示", "还原")) + 16
        pill_w = max(self.chevron._font.measure(label) for label in ("展开详情", "收起详情")) + linespace + 10
        return max(dock_w, pill_w)

    def _sync_latency_style(self) -> None:
        text = self.latency_var.get()
        # 毫秒和「网络正常」同一字号，并像小窗那样紧挨在标题右边。
        self._latency_font = FONT_TITLE
        self.latency.configure(compact=True, text=text, font=FONT_TITLE)

    def _fit_title(self) -> None:
        natural = max(int(self._title_font.measure(self.title_var.get())), 48)
        scale = _ui_scale(self.root)
        width = min(natural, int(460 * scale))
        current = int(float(self.title_label.cget("wraplength")))
        if abs(current - width) > 2:
            self.title_label.configure(wraplength=width)

    def _toggle_mini(self) -> None:
        self._set_mini(not self.mini)

    def _set_mini(self, mini: bool) -> None:
        self.mini = mini
        self.settings["mini"] = mini
        self._persist()
        self._redraw_layout()

    def toggle(self) -> None:
        if self.mini:
            self._set_mini(False)
            return
        self.expanded = not self.expanded
        self.settings["expanded"] = self.expanded
        self._persist()
        self._redraw_layout()

    def _redraw_layout(self) -> None:
        hwnd = _top_hwnd(self.root)
        _set_redraw(hwnd, True)
        try:
            self._layout()
            self._place(first=False)
        finally:
            _set_redraw(hwnd, False)

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
        self.title_label.configure(fg=MUTED if paused else _LEVELS.get(self._diagnosis.level, IDLE))

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
            ("收成小窗" if not self.mini else "还原窗口", self._toggle_mini),
            ("收起详情" if self.expanded else "展开详情", self.toggle),
            ("立即检测", self.kick),
            ("继续监测" if self._paused.is_set() else "暂停监测", self._toggle_pause),
            (f"间隔：{interval_label(self._interval)}", self._cycle_interval),
            ("开机启动：开" if autostart_enabled() else "开机启动：关", self._toggle_autostart),
            ("顶层显示：是" if self.topmost_var.get() else "顶层显示：否", self._toggle_topmost),
            ("丢包率测试", self.deep_loss),
            ("复制诊断结果", self.copy_diagnosis),
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
        if getattr(event.widget, "_selectable", False):
            self._ignore_click = True
            return
        if getattr(event.widget, "_is_pill", False) or getattr(event.widget, "_is_dock", False) or getattr(
            event.widget, "_is_scroll", False
        ) or isinstance(event.widget, (tk.Button, tk.Scrollbar)):
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
        scale = _ui_scale(self.root)
        # 按即将使用的窗口宽度算，不先把窗口拉过去再量。中途改大小会闪一帧。
        side = 34 if self.expanded and not self.mini else 18
        host = self._window_width() - side
        actual = self.detail_host.winfo_width()
        if actual > 80 and abs(actual - host) < 40:
            host = actual
        # 白卡片左右有圆角留白。滚动条在右侧。再留一点，避免「58 ms」的 s 被切掉。
        inset = PANEL_CORNER * 2 + 8
        if reserve_scroll:
            inset += 14 + 4 + 12
        return max(int(180 * scale), host - inset)

    def _window_width(self) -> int:
        """收起时贴着内容；展开时用同一套物理宽度，并限制在屏幕以内。"""
        locked = getattr(self, "_layout_width", None)
        if locked:
            return int(locked)
        scale = _ui_scale(self.root)
        screen_w = max(int(self.root.winfo_screenwidth()), 800)
        content = max(int(self.shell.winfo_reqwidth()), int(180 * scale))
        cap = int(screen_w * 0.72)
        if self.mini or not self.expanded:
            return min(content, cap)
        design = int(round(EXPANDED_WIDTH * scale))
        return min(max(content, design), cap)

    def _capped_width(self, x: int) -> int:
        """优先保住窗口左上角。右边不够时变窄，而不是把整窗往左推。"""
        width = self._window_width()
        screen_w = max(int(self.root.winfo_screenwidth()), 800)
        room = screen_w - max(int(x), 0) - 8
        minimum = int(180 * _ui_scale(self.root))
        if room >= minimum:
            width = min(width, room)
        return width

    def _copy_width(self) -> int:
        return max(140, self._detail_text_width - 28)

    def _set_detail_text(self, text: str) -> None:
        current = self.detail_box.get("1.0", "end-1c")
        if current == text:
            return
        self.detail_box.delete("1.0", "end")
        if text:
            self.detail_box.insert("1.0", text)

    def _fit_detail_text(self) -> None:
        box = self.detail_box
        box.update_idletasks()
        shown = box.count("1.0", "end-1c", "displaylines")
        lines = max(int(shown[0]) if shown else 1, 1)
        if int(box.cget("height")) != lines:
            box.configure(height=lines)

    def _select_detail(self, event: tk.Event) -> str:
        event.widget.tag_add("sel", "1.0", "end-1c")
        event.widget.mark_set("insert", "end-1c")
        return "break"

    def _readonly_key(self, event: tk.Event) -> str | None:
        if event.keysym in {
            "Left",
            "Right",
            "Up",
            "Down",
            "Home",
            "End",
            "Prior",
            "Next",
            "Shift_L",
            "Shift_R",
            "Control_L",
            "Control_R",
        }:
            return None
        if event.state & 0x4 and event.keysym.lower() not in {"v", "x"}:
            return None
        return "break"

    def _apply_detail_width(self, width: int) -> None:
        if width == self._detail_text_width and getattr(self, "_detail_width_ready", False):
            self._fit_detail_text()
            return
        self._detail_width_ready = True
        self._detail_text_width = width
        copy_width = self._copy_width()
        self.spark.configure(width=copy_width)
        self.details.place(x=0, y=-self._scroll_offset, width=width)
        self._placed_detail_width = width

        def walk(widget: tk.Misc) -> None:
            for child in widget.winfo_children():
                # 右侧时延是短标签，不设折行，否则「ms」会被拆开或挤掉。
                if isinstance(child, tk.Label) and int(child.cget("wraplength")) > 0:
                    child.configure(wraplength=copy_width)
                walk(child)

        walk(self.details)
        self._fit_detail_text()

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
                self.detail_scroll.pack(side="right", fill="y", padx=(4, 12), pady=(20, 16))
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
        hwnd = _top_hwnd(self.root)
        _set_redraw(hwnd, True)
        try:
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
            if not self.mini:
                self._fit_title()
            # 先按「左上角不动」定宽度，详情文字按这个宽度折行，避免先画一版再挪一次。
            self._layout_width = self._capped_width(x) if not first else None
            width = self._layout_width or width
            if self.expanded and not self.mini:
                self._fit_details()
                self.root.update_idletasks()
                height = max(self.shell.winfo_reqheight(), 52)
                overflow = y + height - (screen_h - 4)
                if overflow > 0 and self._view_height > 160:
                    self._view_height = max(160, self._view_height - overflow)
                    self.detail_view.configure(height=self._view_height)
                    self._move_details(self._scroll_offset if self._max_offset() > 0 else 0)
                    self.root.update_idletasks()
                    height = max(self.shell.winfo_reqheight(), 52)
            self._layout_width = None
            if x + width > screen_w - 8:
                x = max(8, screen_w - 8 - width)
            if x < 0:
                x = 8
            if y + height > screen_h - 4:
                y = max(8, screen_h - 4 - height)
            if y < 0:
                y = 8
            self.root.geometry(f"{width}x{height}+{x}+{y}")
            _round_window(self.root, CORNER)
            _apply_glass(hwnd)
            if self.expanded and not self.mini:
                self._mask_panel_corners()
        finally:
            self._layout_width = None
            _set_redraw(hwnd, False)

    def _raise(self) -> None:
        self._apply_topmost()

    def _mask_panel_corners(self) -> None:
        radius = PANEL_CORNER
        if not hasattr(self, "_corner_masks"):
            self._corner_masks = {}
            self._corner_photos = {}
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
                    bg=CARD,
                    highlightthickness=0,
                    bd=0,
                )
                canvas.place(relx=relx, rely=rely, anchor=anchor)
                self._corner_masks[key] = canvas
        for key, canvas in self._corner_masks.items():
            photo = _aa_quarter(radius, CARD, PANEL, key)
            self._corner_photos[key] = photo
            canvas.configure(width=radius, height=radius)
            canvas.delete("all")
            canvas.create_image(0, 0, anchor="nw", image=photo)
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
        return "无回复"
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
        return f"{label} 无回复，已等 {PING_WAIT_MS}ms"
    loss = f" 丢{sample.loss_pct}%" if sample.loss_pct else ""
    return f"{label} {sample.avg_ms}ms{loss}"


def _ping_text(sample: PingSample) -> str:
    if sample.sent <= 0:
        return sample.error or "没有数据"
    if not sample.replied:
        return f"无回复 · 已等 {PING_WAIT_MS} ms · 没有测到延迟"
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


class _DetailBar(tk.Canvas):
    """详情区的竖向滚动条。系统滚动条在白卡片上几乎看不见，这里自己画。"""

    def __init__(self, parent: tk.Misc, command) -> None:
        super().__init__(parent, width=14, bg=PANEL, highlightthickness=0, bd=0, cursor="hand2")
        self._is_scroll = True
        self._command = command
        self._first = 0.0
        self._last = 1.0
        self._hot = False
        self._drag: tuple[int, float] | None = None
        self._box = (0, 0, 1, 1.0)
        self.bind("<Button-1>", self._press)
        self.bind("<B1-Motion>", self._drag_move)
        self.bind("<ButtonRelease-1>", self._release)
        self.bind("<Enter>", lambda _event: self._set_hot(True))
        self.bind("<Leave>", lambda _event: self._set_hot(False))
        self.bind("<Configure>", lambda _event: self._redraw())

    def set(self, first: float, last: float) -> None:
        self._first = float(first)
        self._last = float(last)
        self._redraw()

    def _set_hot(self, hot: bool) -> None:
        if hot == self._hot:
            return
        self._hot = hot
        self._redraw()

    def _geom(self) -> tuple[int, int, int, float]:
        height = max(int(self.winfo_height()), 1)
        span = min(max(self._last - self._first, 0.0), 1.0)
        inner = max(height - 8, 1)
        thumb = min(inner, max(42, int(round(span * inner))))
        travel = max(inner - thumb, 1)
        room = max(1.0 - span, 0.001)
        top = 4 + int(round((min(max(self._first, 0.0), room) / room) * travel))
        top = max(4, min(top, 4 + travel))
        return top, top + thumb, travel, room

    def _redraw(self) -> None:
        width = 14
        height = max(int(self.winfo_height()), 1)
        self.delete("all")
        _paint_round(self, 1, 2, width - 1, height - 2, 6, fill="#c5cedd")
        top, bottom, travel, room = self._geom()
        self._box = (top, bottom, travel, room)
        if self._last - self._first >= 0.999:
            return
        _paint_round(self, 2, top, width - 2, bottom, 5, fill="#2f3e68" if self._hot else "#3d4f92")

    def _press(self, event: tk.Event) -> None:
        top, bottom, _travel, _room = self._box
        if event.y < top or event.y > bottom:
            self._command("scroll", -1 if event.y < top else 1, "pages")
            return
        self._drag = (event.y, self._first)

    def _drag_move(self, event: tk.Event) -> None:
        if self._drag is None:
            return
        origin_y, origin_first = self._drag
        _top, _bottom, travel, room = self._box
        delta = (event.y - origin_y) / max(travel, 1) * room
        self._command("moveto", max(0.0, min(1.0, origin_first + delta)))

    def _release(self, _event: tk.Event) -> None:
        self._drag = None


class _Dock(tk.Canvas):
    """收成小窗的按钮。圆角，避免一块方标签贴在窗口边上。"""

    def __init__(self, parent: tk.Misc) -> None:
        super().__init__(parent, bg=CARD, highlightthickness=0, bd=0, cursor="hand2")
        self._is_dock = True
        self._text = "小窗显示"
        self._hot = False
        self._span: int | None = None
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

    def natural_width(self) -> int:
        return self._font.measure(self._text.replace("\n", "")) + 16

    def set_span(self, width: int | None) -> None:
        width = int(width) if width else None
        if width == self._span:
            return
        self._span = width
        self._redraw()

    def _enter(self, _event: tk.Event) -> None:
        self._hot = True
        self._redraw()

    def _leave(self, _event: tk.Event) -> None:
        self._hot = False
        self._redraw()

    def _redraw(self) -> None:
        linespace = max(self._font.metrics("linespace"), 16)
        pad_y = max(8, linespace // 4)
        label = self._text.replace("\n", "")
        width = self._span or self.natural_width()
        height = linespace + pad_y * 2
        super().configure(width=width, height=height)
        self.delete("all")
        radius = height // 2 if self._span else min(height // 2, 8)
        _paint_round(self, 1, 1, width - 1, height - 1, radius, fill=BUTTON_ACTIVE if self._hot else BUTTON)
        self.create_text(width / 2, height / 2, text=label, fill=FG, font=self._font)


class _Readout(tk.Canvas):
    """延迟数字。带一层状态色底，不像直接贴在深色背景上。"""

    def __init__(self, parent: tk.Misc) -> None:
        super().__init__(parent, bg=CARD, highlightthickness=0, bd=0)
        self._text = "…"
        self._fg = IDLE
        self._compact = False
        self._font = tkfont.Font(font=FONT_LATENCY)
        self._slot_font = tkfont.Font(font=FONT_LATENCY)
        self._slot_title = tkfont.Font(font=FONT_TITLE)
        self._redraw()

    def configure(self, cnf=None, **kwargs):  # type: ignore[override]
        if cnf:
            kwargs.update(cnf)
        text = kwargs.pop("text", None)
        fg = kwargs.pop("fg", None)
        font = kwargs.pop("font", None)
        compact = kwargs.pop("compact", None)
        changed = False
        if compact is not None and bool(compact) != self._compact:
            self._compact = bool(compact)
            changed = True
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
        pad_x = 8 if self._compact else 12
        pad_y = 4
        needed = self._font.measure(self._text)
        if not self._compact:
            # 和标题同一字号，仍留出「888 ms」和「无回复」，避免面板来回变宽。
            needed = max(needed, self._font.measure("888 ms"), self._font.measure("无回复"))
        width = max(needed + pad_x * 2, 36 if self._compact else 52)
        line = self._font.metrics("linespace")
        height = max(line + pad_y * 2, 24)
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
        self._span: int | None = None
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

    def natural_width(self) -> int:
        linespace = max(self._font.metrics("linespace"), 16)
        return self._font.measure(self._label) + linespace + 10

    def set_span(self, width: int | None) -> None:
        width = int(width) if width else None
        if width == self._span:
            return
        self._span = width
        self._redraw()

    def _redraw(self) -> None:
        linespace = max(self._font.metrics("linespace"), 16)
        pad_y = max(8, linespace // 4)
        width = self._span or self.natural_width()
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


_BAR_CACHE: dict[tuple[int, int, str, str], tk.PhotoImage] = {}


def _round_bar(canvas: tk.Canvas, x0: float, y0: float, x1: float, y1: float, fill: str) -> None:
    """圆角柱。画布椭圆只有实心和透明，矮柱子的顶角会呈阶梯。"""
    width = max(2, int(round(x1 - x0)))
    height = max(2, int(round(y1 - y0)))
    image = _aa_capsule(width, height, fill, SHEET)
    canvas.create_image(int(round(x0)), int(round(y0)), image=image, anchor="nw")


def _in_round_rect(px: float, py: float, width: int, height: int, radius: float) -> bool:
    """顶角是圆的，底边平。矮柱子不再用椭圆去盖，避免阶梯角。"""
    # 底边贴齐，柱脚是实的；顶和两侧留出半像素，边缘才能混色。
    left, top, right, bottom = 0.65, 0.65, width - 0.65, float(height)
    if px < left or px > right or py < top or py > bottom:
        return False
    limit = min(radius, (right - left) / 2, max((bottom - top) / 2, 0.5))
    if py >= top + limit:
        return True
    cx = min(max(px, left + limit), right - limit)
    dx = px - cx
    dy = py - (top + limit)
    return dx * dx + dy * dy <= limit * limit


def _aa_capsule(width: int, height: int, fill: str, outer: str) -> tk.PhotoImage:
    key = (width, height, fill, outer)
    cached = _BAR_CACHE.get(key)
    if cached is not None:
        return cached
    scale = 6
    samples = scale * scale
    radius = min(width, height) / 2
    image = tk.PhotoImage(width=width, height=height)
    for y in range(height):
        row = []
        for x in range(width):
            hit = 0
            for sy in range(scale):
                for sx in range(scale):
                    px = x + (sx + 0.5) / scale
                    py = y + (sy + 0.5) / scale
                    if _in_round_rect(px, py, width, height, radius):
                        hit += 1
            if hit <= 0:
                row.append(outer)
            elif hit >= samples:
                row.append(fill)
            else:
                row.append(_mix(outer, fill, hit / samples))
        image.put("{" + " ".join(row) + "}", to=(0, y))
    _BAR_CACHE[key] = image
    return image


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


def _top_hwnd(widget: tk.Misc) -> int:
    user = ctypes.windll.user32
    user.GetAncestor.argtypes = [wintypes.HWND, ctypes.c_uint]
    user.GetAncestor.restype = wintypes.HWND
    hwnd = user.GetAncestor(int(widget.winfo_id()), 2)
    return int(hwnd or 0)


def _bgr(color: str) -> int:
    text = color.lstrip("#")
    if len(text) != 6:
        return 0x00141018
    red = int(text[0:2], 16)
    green = int(text[2:4], 16)
    blue = int(text[4:6], 16)
    return (blue << 16) | (green << 8) | red


_QUARTER_CACHE: dict[tuple[int, str, str, str], tk.PhotoImage] = {}


def _aa_quarter(size: int, outer: str, inner: str, quadrant: str) -> tk.PhotoImage:
    """一张抗锯齿的圆角贴片。外侧是窗口底色，内侧是面板色。"""
    key = (size, outer, inner, quadrant)
    cached = _QUARTER_CACHE.get(key)
    if cached is not None:
        return cached
    centers = {
        "nw": (size, size),
        "ne": (0, size),
        "sw": (size, 0),
        "se": (0, 0),
    }
    cx, cy = centers[quadrant]
    scale = 6
    samples = scale * scale
    image = tk.PhotoImage(width=size, height=size)
    for y in range(size):
        row = []
        for x in range(size):
            hit = 0
            for sy in range(scale):
                for sx in range(scale):
                    px = x + (sx + 0.5) / scale
                    py = y + (sy + 0.5) / scale
                    dx = px - cx
                    dy = py - cy
                    if dx * dx + dy * dy <= size * size:
                        hit += 1
            row.append(_mix(outer, inner, hit / samples))
        image.put("{" + " ".join(row) + "}", to=(0, y))
    _QUARTER_CACHE[key] = image
    return image


class _ACCENT_POLICY(ctypes.Structure):
    _fields_ = [
        ("AccentState", ctypes.c_int),
        ("AccentFlags", ctypes.c_int),
        ("GradientColor", ctypes.c_uint),
        ("AnimationId", ctypes.c_int),
    ]


class _COMPOSITION_DATA(ctypes.Structure):
    _fields_ = [
        ("Attribute", ctypes.c_int),
        ("Data", ctypes.c_void_p),
        ("SizeOfData", ctypes.c_size_t),
    ]


_REDRAW_DEPTH = 0
_GLASS_HWND = 0


def _set_redraw(hwnd: int, frozen: bool) -> None:
    """展开过程中先不画。改完大小后一次画完，避免系统把旧画面拉一下。"""
    global _REDRAW_DEPTH
    if not hwnd:
        return
    user = ctypes.WinDLL("user32", use_last_error=True)
    send = user.SendMessageW
    send.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
    send.restype = ctypes.c_void_p
    user.RedrawWindow.argtypes = [wintypes.HWND, ctypes.c_void_p, ctypes.c_void_p, wintypes.UINT]
    user.RedrawWindow.restype = wintypes.BOOL
    if frozen:
        if _REDRAW_DEPTH == 0:
            _disable_resize_animation(hwnd)
            send(hwnd, 11, 0, 0)
        _REDRAW_DEPTH += 1
        return
    _REDRAW_DEPTH = max(0, _REDRAW_DEPTH - 1)
    if _REDRAW_DEPTH:
        return
    send(hwnd, 11, 1, 0)
    # 不要用 LockWindowUpdate。合成窗口一解锁，会先闪一帧空的背景。
    user.RedrawWindow(hwnd, None, None, 0x0001 | 0x0080 | 0x0100 | 0x0400)
    try:
        ctypes.windll.dwmapi.DwmFlush()
    except Exception:
        return


def _disable_resize_animation(hwnd: int) -> None:
    """关掉系统在改窗口大小时的过渡。那一帧会把原来的字拉开。"""
    if getattr(_disable_resize_animation, "done", None) == hwnd:
        return
    try:
        dwm = ctypes.windll.dwmapi
        dwm.DwmSetWindowAttribute.argtypes = [wintypes.HWND, ctypes.c_uint, ctypes.c_void_p, ctypes.c_uint]
        dwm.DwmSetWindowAttribute.restype = ctypes.c_long
        disabled = ctypes.c_int(1)
        dwm.DwmSetWindowAttribute(hwnd, 3, ctypes.byref(disabled), ctypes.sizeof(disabled))
        _disable_resize_animation.done = hwnd
    except Exception:
        return


def _apply_glass(hwnd: int) -> None:
    """亚克力：后面的画面先模糊，再罩一层深色。按钮、文字和白卡片保持原色。"""
    global _GLASS_HWND
    if not hwnd or hwnd == _GLASS_HWND:
        return
    try:
        # AccentState 4 是亚克力。罩色为 0 时只留系统模糊，深色底和白卡片上的字都还在。
        # 再填一层浅色罩，或者整窗乘透明度，字会被冲淡。
        policy = _ACCENT_POLICY(4, 2, 0, 0)
        data = _COMPOSITION_DATA(19, ctypes.addressof(policy), ctypes.sizeof(policy))
        user = ctypes.windll.user32
        set_comp = user.SetWindowCompositionAttribute
        set_comp.argtypes = [wintypes.HWND, ctypes.POINTER(_COMPOSITION_DATA)]
        set_comp.restype = wintypes.BOOL
        set_comp(hwnd, ctypes.byref(data))
        _GLASS_HWND = hwnd
    except Exception:
        log_error(traceback.format_exc())
        return


def _round_window(widget: tk.Misc, radius: int) -> None:
    """用系统合成的圆角。区域裁剪只有实心和透明，大屏幕上边缘会锯齿。"""
    try:
        hwnd = _top_hwnd(widget)
        if not hwnd:
            return
        key = (hwnd, max(int(radius), 1))
        if getattr(widget, "_corner_key", None) == key:
            return
        user = ctypes.windll.user32
        user.SetWindowRgn.argtypes = [wintypes.HWND, ctypes.c_void_p, wintypes.BOOL]
        user.SetWindowRgn.restype = wintypes.BOOL
        user.SetWindowRgn(hwnd, None, True)
        dwm = ctypes.windll.dwmapi
        dwm.DwmSetWindowAttribute.argtypes = [wintypes.HWND, ctypes.c_uint, ctypes.c_void_p, ctypes.c_uint]
        dwm.DwmSetWindowAttribute.restype = ctypes.c_long
        preference = ctypes.c_int(2)
        result = dwm.DwmSetWindowAttribute(hwnd, 33, ctypes.byref(preference), ctypes.sizeof(preference))
        if result != 0:
            _region_round(hwnd, radius)
            return
        try:
            border = ctypes.c_uint(_bgr(str(widget.cget("bg"))))
        except tk.TclError:
            border = ctypes.c_uint(_bgr(CARD))
        dwm.DwmSetWindowAttribute(hwnd, 34, ctypes.byref(border), ctypes.sizeof(border))
        widget._corner_key = key
    except Exception:
        return


def _region_round(hwnd: int, radius: int) -> None:
    user = ctypes.windll.user32
    gdi = ctypes.windll.gdi32
    user.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
    user.SetWindowRgn.argtypes = [wintypes.HWND, wintypes.HRGN, wintypes.BOOL]
    user.SetWindowRgn.restype = wintypes.BOOL
    gdi.CreateRoundRectRgn.restype = wintypes.HRGN
    rect = wintypes.RECT()
    if not user.GetWindowRect(hwnd, ctypes.byref(rect)):
        return
    width = int(rect.right - rect.left)
    height = int(rect.bottom - rect.top)
    if width < 8 or height < 8:
        return
    region = gdi.CreateRoundRectRgn(0, 0, width + 1, height + 1, radius * 2, radius * 2)
    if region:
        user.SetWindowRgn(hwnd, region, True)


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
