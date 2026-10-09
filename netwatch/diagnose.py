from __future__ import annotations

from netwatch.config import PING_WAIT_MS
from netwatch.models import Diagnosis, PingSample, SiteProbe, Snapshot, WifiInfo

# 一轮只有 4 个包。连续 3 个超时，或丢失过半，就视为这一段断了。
DOWN_LOSS = 60
DOWN_STREAK = 3
# 平均 20ms、最长上百毫秒：最长至少是平均的 3 倍，并且超过 100ms。
JITTER_MAX_MS = 100
JITTER_RATIO = 3
WEAK_RSSI = -75
WEAK_SIGNAL = 45
STRONG_RSSI = -65
STRONG_SIGNAL = 70


def diagnose(snapshot: Snapshot) -> Diagnosis:
    if snapshot.error:
        return Diagnosis("bad", "检测失败", snapshot.error, "检测过程出错")

    gateway = snapshot.gateway_ping
    internet = snapshot.internet_ping
    wifi = snapshot.wifi

    if not snapshot.lan_gateway:
        return Diagnosis(
            "bad",
            "电脑→路由器 未连接",
            _points(
                [
                    "未检测到局域网网关。",
                    "电脑当前可能未连接无线网络或网线。",
                ]
            ),
            "没有局域网网关",
        )

    if gateway.sent == 0 and internet.sent == 0:
        return Diagnosis(
            "bad",
            "Ping 没有执行",
            _points([gateway.error or internet.error or "本轮连通性测试未能执行。"]),
            "ping 没有返回结果",
        )

    gateway_down = _segment_down(gateway)
    internet_down = _segment_down(internet)

    if snapshot.tun_likely and internet_down and not gateway_down:
        points = [
            f"到路由器 {snapshot.lan_gateway} 的测试已收到回复。",
            "到 223.5.5.5 的测试未收到回复。",
            (
                f"当前默认路由指向 {snapshot.default_gateway or 'VPN'}，"
                "测试流量可能进入 VPN 隧道，不能据此判断路由器或运营商故障。"
            ),
        ]
        return _finish("warn", "路由器→互联网 没有回应", points, snapshot, "默认路由在 VPN 上，不能据此判断路由器")

    if gateway_down and internet_down:
        points = [
            "到路由器与到 223.5.5.5 的测试均超时。",
            "两段同时无响应，故障位于电脑到路由器这一段。",
            _wifi_facts(wifi),
        ]
        return _finish("bad", "电脑→路由器 链路不通", points, snapshot, "电脑到路由器、路由器到互联网同时超时")

    # 路由器常常不回答 ping，但照样转发。互联网有回复时，不能把这一段沉默当成 Wi-Fi 断了。
    silent = _router_ignores_ping(gateway, internet)
    seen_gateway = PingSample(host=gateway.host) if silent else gateway

    if gateway_down and not silent:
        if gateway.received <= 0:
            points = [
                f"到路由器的 ping 没有回应。所有测试包均超时，未测得延迟。每个测试包等待 {PING_WAIT_MS} ms。",
                "到互联网的测试亦未收到正常回复。",
                "故障位于无线网络或路由器。",
                _wifi_facts(wifi),
            ]
        else:
            points = [
                "到路由器的测试出现连续超时。",
                "故障位于无线网络或路由器。",
                _wifi_facts(wifi),
            ]
        return _finish("bad", "电脑→路由器 链路不通", points, snapshot, "只有电脑到路由器这一段超时")

    if internet_down:
        if gateway.loss_pct == 0 and gateway.max_timeout_streak == 0 and gateway.replied:
            points = [
                "到路由器的测试持续收到回复，未出现丢包或连续超时。",
                "到 223.5.5.5 的测试超时。",
                "故障位于路由器到互联网这一段，可能与路由器或运营商有关。",
            ]
        else:
            points = [
                "到路由器的测试仍有回复。",
                "到 223.5.5.5 的测试基本超时。",
                "主要故障位于路由器到互联网这一段。",
            ]
        return _finish("bad", "路由器→互联网 链路不通", points, snapshot, "路由器通，只有到 223.5.5.5 超时")

    weak = _signal_weak(wifi)
    symptoms = (
        _jitter(seen_gateway)
        or _jitter(internet)
        or seen_gateway.loss_pct >= 5
        or internet.loss_pct >= 5
        or seen_gateway.max_timeout_streak >= 2
        or internet.max_timeout_streak >= 2
    )
    if weak and symptoms:
        points = [
            _wifi_facts(wifi),
            "信号偏弱时，时延升高和丢包会首先出现在电脑到路由器这一段。",
        ]
        return _finish("warn", "电脑→路由器 信号偏弱", points, snapshot, "信号弱，并且已经出现丢包或抖动")

    interference = _interference(wifi, seen_gateway, internet)
    if interference:
        points = [
            _wifi_facts(wifi),
            "当前信号强度并不弱。2.4 GHz 频段易受邻近网络干扰，通常表现为时延突然升高或连续数个测试包超时，而网卡仍显示已连接。",
            _jitter_clause(internet),
        ]
        return _finish("warn", "电脑→路由器 2.4GHz 干扰", points, snapshot, "2.4GHz 且信号不弱，但出现抖动或连续超时")

    if _jitter(internet) or _jitter(seen_gateway):
        if _jitter(internet):
            title = "路由器→互联网 链路抖动"
            rule = "路由器不回答 ping，到互联网出现抖动" if silent else "到互联网的最长远高于平均"
        else:
            title = "电脑→路由器 链路抖动"
            rule = "电脑到路由器的最长远高于平均"
        return _finish("warn", title, _jitter_points(gateway, internet), snapshot, rule)

    if seen_gateway.loss_pct >= 5:
        points = [
            f"电脑到路由器的丢包率为 {gateway.loss_pct}%。",
            "建议优先检查无线网络或路由器。",
        ]
        return _finish("warn", "电脑→路由器 链路丢包", points, snapshot, "电脑到路由器丢包达到 5%")

    if internet.loss_pct >= 1:
        level = "bad" if internet.loss_pct >= 20 else "warn"
        points = [
            "到路由器的测试正常。",
            f"到 223.5.5.5 的丢包率为 {internet.loss_pct}%。",
            "丢包发生在路由器之外，更可能与路由器出口或运营商有关。",
        ]
        return _finish(level, "路由器→互联网 链路丢包", points, snapshot, "路由器正常，到 223.5.5.5 有丢包")

    if weak:
        points = [
            _wifi_facts(wifi),
            "本轮连通性测试结果正常，但无线信号余量不足，距离路由器较远时容易出现波动。",
        ]
        return _finish("warn", "电脑→路由器 信号偏弱", points, snapshot, "信号弱，这次 ping 仍正常")

    router = snapshot.lan_gateway
    internet_host = internet.host or "223.5.5.5"
    if silent:
        points = [
            (
                f"到路由器 {router} 的 ping 没有回应。"
                f"所有测试包均超时，未测得延迟。每个测试包等待 {PING_WAIT_MS} ms。"
            ),
            _internet_reply(internet_host, internet),
            "路由器当前可正常转发，但不回应 ping 测试请求。",
        ]
        return _finish("ok", "网络正常", points, snapshot, "路由器不回答 ping，到互联网有回复")
    points = [
        f"到路由器（{router}）的测试已收到回复。",
        f"到互联网（{internet_host}）的测试已收到回复。",
        "两段时延稳定，未出现丢包。",
    ]
    return _finish("ok", "网络正常", points, snapshot, "两边都通，延迟稳定，没有丢包")


def _finish(level: str, title: str, points: list[str], snapshot: Snapshot, rule: str) -> Diagnosis:
    site_title, site_level, site_notes, site_rule = _site_finding(snapshot)
    if site_title and title == "网络正常":
        title = site_title
        level = site_level
        rule = site_rule
    items = [item for item in points if item] + site_notes
    if snapshot.tun_likely and not any("隧道" in item or "VPN" in item for item in items):
        items.append(
            f"当前默认路由为 {snapshot.default_gateway or 'VPN'}，并非局域网路由器 {snapshot.lan_gateway}。"
        )
    return Diagnosis(level, title, _points(items), rule)


def _site_finding(snapshot: Snapshot) -> tuple[str, str, list[str], str]:
    domestic = [site for site in snapshot.sites if not site.foreign]
    foreign = [site for site in snapshot.sites if site.foreign]
    proxy = snapshot.proxy
    notes: list[str] = []
    title = ""
    level = "warn"
    rule = ""

    link_up = snapshot.internet_ping.replied and snapshot.internet_ping.loss_pct < 20
    if domestic and all(not site.ok for site in domestic) and link_up:
        if all(site.error.startswith("DNS") for site in domestic):
            title, level, rule = "DNS 解析失败", "bad", "地址能 ping 通，但域名解析失败"
            notes.append("到 223.5.5.5 的连通测试不经过域名解析。")
            notes.append("国内网站访问失败，更符合 DNS 解析异常。")
        else:
            title, rule = "国内网站不通", "地址能 ping 通，但国内网页没有回应"
            notes.append("国内地址的连通测试已收到回复，但网页访问没有响应。")
            notes.append("可能与 DNS 解析或目标网站本身有关。")
    elif domestic and link_up and any(not site.ok for site in domestic):
        failed = "、".join(site.name for site in domestic if not site.ok)
        notes.append(f"{failed} 本次访问未成功。")

    if proxy.probe_foreign:
        if foreign and all(not site.ok for site in foreign) and any(site.ok for site in domestic):
            if not title:
                title, level, rule = "代理网络不通", "warn", "国内网页通，国外网页不通"
            notes.append("国内网站访问正常，国外网站访问失败。")
            notes.append("代理可能未成功连接节点，或规则未将国外流量转发至代理。")
        elif foreign and all(site.ok for site in foreign) and title == "":
            opened = [site for site in foreign if site.ms is not None]
            domestic_fast = domestic and all(site.ok and (site.ms or 0) < 400 for site in domestic)
            fastest = min((site.ms for site in opened), default=None)
            if opened and fastest is not None and fastest >= 1200 and domestic_fast:
                title, rule = "代理延迟偏高", "国外网站都能打开，但延迟都超过 1.2 秒"
                notes.append("国外网站可以打开，但时延均较高。")
                notes.append("更符合代理线路时延偏高，而非本地宽带故障。")
            else:
                slow_names = [site.name for site in opened if site.ms is not None and site.ms >= 1500]
                if slow_names and domestic_fast:
                    notes.append(f"{'、'.join(slow_names)} 本次访问时延明显偏高，其余国外站点访问正常。")
                else:
                    notes.append("网络代理已开启，国外网站可正常访问。")
    elif proxy.clash_running:
        notes.append("Clash 正在运行，但系统代理未开启，因此本轮仅测试了国内网站。")

    return title, level, notes, rule


def _router_ignores_ping(gateway: PingSample, internet: PingSample) -> bool:
    """路由器一个 ping 都不回，但互联网这一段是通的。"""
    return gateway.sent > 0 and gateway.received <= 0 and internet.replied and not _segment_down(internet)


def _segment_down(sample: PingSample) -> bool:
    if sample.sent <= 0:
        return False
    if sample.received <= 0 or sample.loss_pct >= DOWN_LOSS:
        return True
    return sample.max_timeout_streak >= DOWN_STREAK


def _jitter(sample: PingSample) -> bool:
    if sample.avg_ms is None or sample.max_ms is None or sample.received <= 0:
        return False
    return sample.max_ms >= JITTER_MAX_MS and sample.max_ms >= sample.avg_ms * JITTER_RATIO


def _signal_weak(wifi: WifiInfo | None) -> bool:
    if wifi is None or not wifi.connected:
        return False
    if wifi.rssi is not None and wifi.rssi <= WEAK_RSSI:
        return True
    return wifi.signal is not None and wifi.signal < WEAK_SIGNAL


def _signal_strong(wifi: WifiInfo | None) -> bool:
    if wifi is None or not wifi.connected:
        return False
    if wifi.signal is not None and wifi.signal >= STRONG_SIGNAL:
        return True
    return wifi.rssi is not None and wifi.rssi >= STRONG_RSSI


def _interference(wifi: WifiInfo | None, gateway: PingSample, internet: PingSample) -> bool:
    if wifi is None or not wifi.connected or "2.4" not in (wifi.band or ""):
        return False
    if not _signal_strong(wifi):
        return False
    burst = gateway.max_timeout_streak >= 2 or internet.max_timeout_streak >= 2
    return _jitter(internet) or _jitter(gateway) or burst


def _wifi_facts(wifi: WifiInfo | None) -> str:
    if wifi is None or not wifi.connected:
        return ""
    parts = [f"当前连接无线网络 {wifi.ssid or '未命名网络'}"]
    if wifi.band:
        parts.append(f"频段 {wifi.band}")
    if wifi.channel:
        parts.append(f"信道 {wifi.channel}")
    if wifi.width:
        parts.append(wifi.width)
    if wifi.radio:
        parts.append(wifi.radio)
    sentence = "，".join(parts)
    signal_bits = []
    if wifi.signal is not None:
        signal_bits.append(f"信号 {wifi.signal}%")
    if wifi.rssi is not None:
        signal_bits.append(f"RSSI {wifi.rssi}")
    if wifi.rx_mbps is not None:
        signal_bits.append(f"协商速率 {wifi.rx_mbps:g} Mbps")
    if signal_bits:
        sentence += "，" + "，".join(signal_bits)
    return sentence + "。"


def _internet_reply(host: str, sample: PingSample) -> str:
    bits = [f"到 {host} 的测试已收到回复"]
    if sample.avg_ms is not None:
        bits.append(f"平均时延 {sample.avg_ms} ms")
    if sample.max_ms is not None and sample.max_ms != sample.avg_ms:
        bits.append(f"最长时延 {sample.max_ms} ms")
    if len(bits) == 1:
        return bits[0] + "。"
    return bits[0] + "，" + "，".join(bits[1:]) + "。"


def _jitter_clause(sample: PingSample) -> str:
    if sample.avg_ms is None or sample.max_ms is None:
        return ""
    return f"到 223.5.5.5 平均时延 {sample.avg_ms} ms，最长时延 {sample.max_ms} ms。"


def _jitter_points(gateway: PingSample, internet: PingSample) -> list[str]:
    if _jitter(internet) and internet.avg_ms is not None and internet.max_ms is not None:
        if gateway.received <= 0 and gateway.sent > 0:
            return [
                (
                    "到路由器的 ping 没有回应。"
                    f"所有测试包均超时，未测得延迟。每个测试包等待 {PING_WAIT_MS} ms。"
                ),
                f"到 223.5.5.5 平均时延 {internet.avg_ms} ms，最长时延 {internet.max_ms} ms，时延波动明显。",
            ]
        return [
            "两段测试均收到回复，但最长时延明显高于平均时延。",
            f"到 223.5.5.5 平均时延 {internet.avg_ms} ms，最长时延 {internet.max_ms} ms。",
        ]
    if gateway.avg_ms is not None and gateway.max_ms is not None:
        return [
            "到互联网的时延较为稳定。",
            f"电脑到路由器的时延出现明显升高：平均时延 {gateway.avg_ms} ms，最长时延 {gateway.max_ms} ms。",
            "更符合无线链路抖动的特征。",
        ]
    return ["时延出现明显升高，属于链路抖动。"]


def _points(items: list[str]) -> str:
    lines = [item.strip() for item in items if item and item.strip()]
    if not lines:
        return ""
    if len(lines) == 1:
        return lines[0]
    return "\n".join(f"{index}. {line}" for index, line in enumerate(lines, 1))
