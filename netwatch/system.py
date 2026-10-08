from __future__ import annotations

import socket
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request
import winreg
from concurrent.futures import ThreadPoolExecutor

from netwatch.config import (
    DOMESTIC_SITES,
    FOREIGN_SITES,
    HTTP_TIMEOUT_SEC,
    INTERNET_HOST,
    PING_COUNT,
    PING_WAIT_MS,
    PROXY_PORTS,
)
from netwatch.models import PingSample, ProxyState, SiteProbe, Snapshot, WifiInfo
from netwatch.parse import (
    choose_lan_gateway,
    is_private,
    parse_adapters,
    parse_default_route,
    parse_ping,
    parse_wifi,
)

_CREATE_NO_WINDOW = 0x08000000


def run_cmd(args: list[str], timeout: float) -> str:
    proc = subprocess.run(
        args,
        capture_output=True,
        timeout=timeout,
        creationflags=_CREATE_NO_WINDOW,
    )
    return _decode(proc.stdout or proc.stderr)


def collect() -> Snapshot:
    try:
        lan, default_gw, tun = discover_gateways()
    except Exception as exc:
        return Snapshot(error=f"读取网关失败：{exc}", collected_at=time.time())

    with ThreadPoolExecutor(max_workers=6) as pool:
        wifi_future = pool.submit(_safe_wifi)
        proxy_future = pool.submit(read_proxy, tun)
        gateway_future = pool.submit(ping_host, lan, PING_COUNT)
        internet_future = pool.submit(ping_host, INTERNET_HOST, PING_COUNT)
        site_futures = [
            pool.submit(probe_site, name, url, False, None) for name, url in DOMESTIC_SITES
        ]
        proxy = proxy_future.result()
        if proxy.probe_foreign:
            for name, url in FOREIGN_SITES:
                site_futures.append(pool.submit(probe_site, name, url, True, proxy.http_proxy))
        sites = [future.result() for future in site_futures]
        wifi = wifi_future.result()
        gateway_ping = gateway_future.result()
        internet_ping = internet_future.result()

    return Snapshot(
        lan_gateway=lan,
        default_gateway=default_gw,
        tun_likely=tun,
        gateway_ping=gateway_ping,
        internet_ping=internet_ping,
        wifi=wifi,
        sites=sites,
        proxy=proxy,
        collected_at=time.time(),
    )


def discover_gateways() -> tuple[str, str, bool]:
    adapters = parse_adapters(run_cmd(["ipconfig"], timeout=8))
    route = parse_default_route(run_cmd(["route", "print", "-4"], timeout=8))
    default_gw = route[0] if route else ""
    interface_ip = route[1] if route else ""
    lan = choose_lan_gateway(adapters, interface_ip)
    if not lan and default_gw and is_private(default_gw):
        lan = default_gw
    tun = bool(lan and default_gw and default_gw != lan)
    return lan, default_gw, tun


def ping_host(host: str, count: int = PING_COUNT) -> PingSample:
    if not host:
        sample = PingSample(host)
        sample.error = "没有地址"
        return sample
    try:
        text = run_cmd(
            ["ping", "-n", str(count), "-w", str(PING_WAIT_MS), host],
            timeout=count + 12,
        )
    except Exception as exc:
        sample = PingSample(host)
        sample.error = str(exc)
        return sample
    return parse_ping(text, host)


def read_proxy(tun_likely: bool) -> ProxyState:
    enabled, server = _registry_proxy()
    processes = _clash_processes()
    listen_port = None
    if processes and not (enabled and server):
        listen_port = _first_open_port()
    return ProxyState(
        system_enabled=enabled,
        server=server,
        processes=processes,
        listen_port=listen_port,
        tun_likely=tun_likely,
    )


def probe_site(name: str, url: str, foreign: bool, proxy: str | None) -> SiteProbe:
    host = site_host(url)
    ip = lookup_ipv4(host)
    if proxy:
        handler = urllib.request.ProxyHandler({"http": proxy, "https": proxy})
    else:
        handler = urllib.request.ProxyHandler({})
    opener = urllib.request.build_opener(handler)
    request = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    started = time.perf_counter()
    try:
        with opener.open(request, timeout=HTTP_TIMEOUT_SEC) as response:
            response.read(128)
            ms = int((time.perf_counter() - started) * 1000)
            return SiteProbe(name, True, ms, "", foreign, host, ip)
    except urllib.error.HTTPError as exc:
        ms = int((time.perf_counter() - started) * 1000)
        if foreign and exc.code >= 500:
            return SiteProbe(name, False, ms, f"HTTP {exc.code}", foreign, host, ip)
        return SiteProbe(name, True, ms, "", foreign, host, ip)
    except Exception as exc:
        ms = int((time.perf_counter() - started) * 1000)
        return SiteProbe(name, False, ms, _short_error(exc), foreign, host, ip)


def site_host(url: str) -> str:
    return urllib.parse.urlparse(url).hostname or ""


def lookup_ipv4(host: str) -> str:
    """本机 DNS 解析。失败时返回空字符串，界面上就不显示 IP。"""
    if not host:
        return ""
    try:
        socket.inet_aton(host)
        return host
    except OSError:
        pass
    try:
        records = socket.getaddrinfo(host, None, socket.AF_INET, socket.SOCK_STREAM)
    except OSError:
        return ""
    if not records:
        return ""
    return records[0][4][0]


def _safe_wifi() -> WifiInfo | None:
    try:
        return parse_wifi(run_cmd(["netsh", "wlan", "show", "interfaces"], timeout=8))
    except Exception:
        return None


def _registry_proxy() -> tuple[bool, str]:
    path = r"Software\Microsoft\Windows\CurrentVersion\Internet Settings"
    try:
        key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, path)
    except OSError:
        return False, ""
    with key:
        try:
            enabled = int(winreg.QueryValueEx(key, "ProxyEnable")[0]) == 1
        except OSError:
            enabled = False
        try:
            server = str(winreg.QueryValueEx(key, "ProxyServer")[0]).strip()
        except OSError:
            server = ""
    return enabled, server


def _clash_processes() -> list[str]:
    try:
        text = run_cmd(["tasklist", "/FO", "CSV", "/NH"], timeout=12)
    except Exception:
        return []
    found: list[str] = []
    for line in text.splitlines():
        if not line.startswith('"'):
            continue
        name = line.split('"', 2)[1]
        folded = name.lower()
        if ("clash" in folded or "mihomo" in folded) and name not in found:
            found.append(name)
    return found


def _first_open_port() -> int | None:
    for port in PROXY_PORTS:
        sock = socket.socket()
        sock.settimeout(0.12)
        try:
            sock.connect(("127.0.0.1", port))
        except OSError:
            sock.close()
            continue
        sock.close()
        return port
    return None


def _short_error(exc: Exception) -> str:
    text = str(exc)
    folded = text.lower()
    if "timed out" in folded or "超时" in text:
        return "超时"
    if "getaddrinfo" in folded or "11001" in text or "名称或服务" in text:
        return "DNS 解析失败"
    if "10061" in text or "拒绝" in text:
        return "连接被拒绝"
    return "连接失败"


def _decode(data: bytes) -> str:
    if not data:
        return ""
    for encoding in ("gbk", "utf-8"):
        try:
            text = data.decode(encoding)
        except UnicodeDecodeError:
            continue
        if any(token in text for token in ("已发送", "状态", "SSID", "Sent", "Packets", "0.0.0.0")):
            return text
    return data.decode("gbk", errors="replace")
