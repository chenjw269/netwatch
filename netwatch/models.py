from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class PingSample:
    host: str
    sent: int = 0
    received: int = 0
    loss_pct: int = 0
    min_ms: int | None = None
    max_ms: int | None = None
    avg_ms: int | None = None
    rtts: list[int | None] = field(default_factory=list)
    max_timeout_streak: int = 0
    error: str = ""

    @property
    def replied(self) -> bool:
        return self.received > 0 and not self.error


@dataclass
class WifiInfo:
    ssid: str = ""
    band: str = ""
    channel: str = ""
    width: str = ""
    radio: str = ""
    signal: int | None = None
    rssi: int | None = None
    rx_mbps: float | None = None
    connected: bool = False


@dataclass
class SiteProbe:
    name: str
    ok: bool
    ms: int | None = None
    error: str = ""
    foreign: bool = False
    host: str = ""
    ip: str = ""


@dataclass
class ProxyState:
    system_enabled: bool = False
    server: str = ""
    processes: list[str] = field(default_factory=list)
    listen_port: int | None = None
    tun_likely: bool = False

    @property
    def system_proxy_on(self) -> bool:
        return self.system_enabled and bool(self.server.strip())

    @property
    def clash_running(self) -> bool:
        return bool(self.processes)

    @property
    def probe_foreign(self) -> bool:
        """Clash 系统代理或 TUN 打开时才测国外网站。"""
        return self.system_proxy_on or self.tun_likely

    @property
    def http_proxy(self) -> str | None:
        if not self.system_proxy_on:
            return None
        return normalize_proxy(self.server)


@dataclass
class Snapshot:
    lan_gateway: str = ""
    default_gateway: str = ""
    tun_likely: bool = False
    gateway_ping: PingSample = field(default_factory=lambda: PingSample(""))
    internet_ping: PingSample = field(default_factory=lambda: PingSample("223.5.5.5"))
    wifi: WifiInfo | None = None
    sites: list[SiteProbe] = field(default_factory=list)
    proxy: ProxyState = field(default_factory=ProxyState)
    error: str = ""
    collected_at: float = 0.0


@dataclass
class Diagnosis:
    level: str
    title: str
    detail: str
    rule: str = ""


def normalize_proxy(server: str) -> str:
    text = server.strip()
    if "=" in text:
        parts: dict[str, str] = {}
        for item in text.split(";"):
            if "=" not in item:
                continue
            key, value = item.split("=", 1)
            parts[key.strip().lower()] = value.strip()
        text = parts.get("https") or parts.get("http") or text
    if not text.startswith(("http://", "https://", "socks5://", "socks://")):
        text = "http://" + text
    return text
