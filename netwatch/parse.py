from __future__ import annotations

import re

from netwatch.models import PingSample, WifiInfo

_TIME_RE = re.compile(r"(?:时间|time)\s*[=<]\s*(\d+)\s*ms", re.IGNORECASE)
_LOSS_RE = re.compile(r"[（(](\d+)\s*%\s*(?:丢失|loss)[)）]", re.IGNORECASE)
_SENT_RE = re.compile(r"(?:已发送|Sent)\s*=\s*(\d+)", re.IGNORECASE)
_RECV_RE = re.compile(r"(?:已接收|Received)\s*=\s*(\d+)", re.IGNORECASE)
_MIN_RE = re.compile(r"(?:最短|Minimum)\s*=\s*(\d+)\s*ms", re.IGNORECASE)
_MAX_RE = re.compile(r"(?:最长|Maximum)\s*=\s*(\d+)\s*ms", re.IGNORECASE)
_AVG_RE = re.compile(r"(?:平均|Average)\s*=\s*(\d+)\s*ms", re.IGNORECASE)
_IPV4_RE = re.compile(r"\b(\d{1,3}(?:\.\d{1,3}){3})\b")
_KV_RE = re.compile(r"^\s*(.+?)\s*[:：]\s*(.*)$")

_TIMEOUT_MARKERS = (
    "请求超时",
    "传输失败",
    "无法访问目标主机",
    "目的主机无法访问",
    "timed out",
    "destination host unreachable",
    "transmit failed",
)


def timeout_streak(rtts: list[int | None]) -> int:
    best = current = 0
    for value in rtts:
        if value is None:
            current += 1
            best = max(best, current)
        else:
            current = 0
    return best


def parse_ping(text: str, host: str) -> PingSample:
    lowered_lines = text.splitlines()
    rtts: list[int | None] = []
    for line in lowered_lines:
        folded = line.lower()
        if any(marker in folded or marker in line for marker in _TIMEOUT_MARKERS):
            rtts.append(None)
            continue
        if "时间<1" in line or "time<1" in folded:
            rtts.append(1)
            continue
        match = _TIME_RE.search(line)
        if match:
            rtts.append(int(match.group(1)))

    sent = _first_int(_SENT_RE, text)
    received = _first_int(_RECV_RE, text)
    loss = _first_int(_LOSS_RE, text)
    if sent is None and rtts:
        sent = len(rtts)
    if received is None and rtts:
        received = sum(1 for item in rtts if item is not None)
    if loss is None and sent:
        loss = round(100 * (sent - (received or 0)) / sent)

    sample = PingSample(
        host=host,
        sent=sent or 0,
        received=received or 0,
        loss_pct=loss or 0,
        min_ms=_first_int(_MIN_RE, text),
        max_ms=_first_int(_MAX_RE, text),
        avg_ms=_first_int(_AVG_RE, text),
        rtts=rtts,
        max_timeout_streak=timeout_streak(rtts),
    )
    if sample.sent == 0:
        if "找不到主机" in text or "could not find host" in text.lower():
            sample.error = "找不到主机"
        elif text.strip():
            sample.error = "ping 没有返回统计"
        else:
            sample.error = "ping 没有输出"
    elif not rtts and sample.received == 0:
        sample.max_timeout_streak = sample.sent
    return sample


def parse_default_route(text: str) -> tuple[str, str] | None:
    """返回 (网关, 接口地址)，取跃点数最小的默认路由。"""
    best: tuple[int, str, str] | None = None
    for line in text.splitlines():
        parts = line.split()
        if len(parts) < 5 or parts[0] != "0.0.0.0" or parts[1] != "0.0.0.0":
            continue
        gateway = parts[2]
        if gateway.count(".") != 3:
            continue
        try:
            metric = int(parts[-1])
        except ValueError:
            continue
        interface = parts[3]
        if best is None or metric < best[0]:
            best = (metric, gateway, interface)
    if best is None:
        return None
    return best[1], best[2]


class AdapterInfo:
    def __init__(self, name: str) -> None:
        self.name = name
        self.disconnected = False
        self.ipv4 = ""
        self.gateways: list[str] = []


def parse_adapters(text: str) -> list[AdapterInfo]:
    adapters: list[AdapterInfo] = []
    current: AdapterInfo | None = None
    capture_gateway = False
    for line in text.splitlines():
        if line and not line.startswith((" ", "\t")):
            current = AdapterInfo(line.strip().rstrip(":"))
            adapters.append(current)
            capture_gateway = False
            continue
        if current is None:
            continue
        stripped = line.strip()
        if "媒体状态" in line or "Media State" in line:
            if "断开" in line or "disconnected" in line.lower():
                current.disconnected = True
        if "IPv4" in line or "IP Address" in line:
            found = _IPV4_RE.findall(line)
            if found and not current.ipv4:
                current.ipv4 = found[0]
        if "默认网关" in line or "Default Gateway" in line:
            capture_gateway = True
            current.gateways.extend(_IPV4_RE.findall(line))
            continue
        if capture_gateway:
            found = _IPV4_RE.findall(stripped)
            if found and stripped == found[0]:
                current.gateways.extend(found)
                continue
            if ":" in line or "：" in line:
                capture_gateway = False
    return adapters


def choose_lan_gateway(adapters: list[AdapterInfo], interface_ip: str) -> str:
    private = [item for item in adapters if _private_gateways(item)]
    if interface_ip:
        for item in private:
            if item.ipv4 == interface_ip:
                return _private_gateways(item)[0]
    for item in private:
        if not item.disconnected:
            return _private_gateways(item)[0]
    return ""


def parse_wifi(text: str) -> WifiInfo | None:
    blocks = re.split(r"(?=^\s*(?:名称|Name)\s*[:：])", text, flags=re.MULTILINE)
    connected: list[WifiInfo] = []
    for block in blocks:
        info = _parse_wifi_block(block)
        if info and info.connected:
            connected.append(info)
    if connected:
        return connected[0]
    return None


def _parse_wifi_block(text: str) -> WifiInfo | None:
    fields: dict[str, str] = {}
    for line in text.splitlines():
        match = _KV_RE.match(line)
        if not match:
            continue
        canon = _canon_wifi_key(match.group(1).strip().lower())
        if canon and canon not in fields:
            fields[canon] = match.group(2).strip()
    if not fields:
        return None
    state = fields.get("state", "")
    folded = state.lower()
    if "断开" in state or "disconnect" in folded:
        connected = False
    elif "已连接" in state or folded == "connected" or folded.startswith("connected"):
        connected = True
    else:
        connected = False
    signal = _first_number(fields.get("signal", ""))
    rssi = _signed_number(fields.get("rssi", ""))
    rx = _float_number(fields.get("rx", ""))
    return WifiInfo(
        ssid=fields.get("ssid", ""),
        band=fields.get("band", ""),
        channel=fields.get("channel", ""),
        width=fields.get("width", ""),
        radio=fields.get("radio", ""),
        signal=signal,
        rssi=rssi,
        rx_mbps=rx,
        connected=connected,
    )


def _canon_wifi_key(key: str) -> str:
    if key == "ssid":
        return "ssid"
    if key in {"状态", "state"}:
        return "state"
    if "波段" in key or key == "band":
        return "band"
    if key in {"通道", "信道", "channel"}:
        return "channel"
    if "宽度" in key or "channel width" in key:
        return "width"
    if "无线电" in key or "radio type" in key:
        return "radio"
    if key.startswith("接收速率") or "receive rate" in key:
        return "rx"
    if key.startswith("信号") or key == "signal":
        return "signal"
    if key == "rssi":
        return "rssi"
    return ""


def _private_gateways(adapter: AdapterInfo) -> list[str]:
    return [ip for ip in adapter.gateways if is_private(ip)]


def is_private(ip: str) -> bool:
    parts = ip.split(".")
    if len(parts) != 4:
        return False
    try:
        nums = [int(part) for part in parts]
    except ValueError:
        return False
    if nums[0] == 10:
        return True
    if nums[0] == 192 and nums[1] == 168:
        return True
    if nums[0] == 172 and 16 <= nums[1] <= 31:
        return True
    return False


def _first_int(pattern: re.Pattern[str], text: str) -> int | None:
    match = pattern.search(text)
    if not match:
        return None
    return int(match.group(1))


def _first_number(text: str) -> int | None:
    match = re.search(r"(\d+)", text)
    return int(match.group(1)) if match else None


def _signed_number(text: str) -> int | None:
    match = re.search(r"(-?\d+)", text)
    return int(match.group(1)) if match else None


def _float_number(text: str) -> float | None:
    match = re.search(r"(\d+(?:\.\d+)?)", text)
    return float(match.group(1)) if match else None
