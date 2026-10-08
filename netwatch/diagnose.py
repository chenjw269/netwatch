from __future__ import annotations

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
        return Diagnosis("bad", "没有找到路由器", "没有局域网网关，电脑可能没连上 Wi-Fi 或网线。", "没有局域网网关")

    if gateway.sent == 0 and internet.sent == 0:
        return Diagnosis("bad", "Ping 没有执行", gateway.error or internet.error or "无法运行 ping。", "ping 没有返回结果")

    gateway_down = _segment_down(gateway)
    internet_down = _segment_down(internet)

    if snapshot.tun_likely and internet_down and not gateway_down:
        detail = (
            f"到路由器 {snapshot.lan_gateway} 是通的，到 223.5.5.5 没有回应。"
            f"不过默认路由现在走到 {snapshot.default_gateway or 'VPN'}，"
            "这一段 ping 可能进了隧道，不能据此判断路由器。"
        )
        return _finish("warn", "外网地址没有回应", detail, snapshot, "默认路由在 VPN 上，不能据此判断路由器")

    if gateway_down and internet_down:
        detail = "到路由器和到 223.5.5.5 同时超时。两边同时超时，就是本地无线或路由器。"
        detail = _join(detail, _wifi_facts(wifi))
        return _finish("bad", "本地无线或路由器", detail, snapshot, "电脑到路由器、路由器到互联网同时超时")

    if gateway_down:
        detail = "到路由器出现连续超时。这里超时，问题在 Wi-Fi 或路由器。"
        detail = _join(detail, _wifi_facts(wifi))
        return _finish("bad", "Wi-Fi 或路由器", detail, snapshot, "只有电脑到路由器这一段超时")

    if internet_down:
        if gateway.loss_pct == 0 and gateway.max_timeout_streak == 0 and gateway.replied:
            detail = "网关一直通、只有到 223.5.5.5 这一段超时，问题在路由器或运营商。"
        else:
            detail = "到路由器还有回复，到 223.5.5.5 基本超时。主要问题在路由器或运营商。"
        return _finish("bad", "路由器或运营商", detail, snapshot, "路由器通，只有到 223.5.5.5 超时")

    weak = _signal_weak(wifi)
    symptoms = (
        _jitter(gateway)
        or _jitter(internet)
        or gateway.loss_pct >= 5
        or internet.loss_pct >= 5
        or gateway.max_timeout_streak >= 2
        or internet.max_timeout_streak >= 2
    )
    if weak and symptoms:
        detail = _join(_wifi_facts(wifi), "信号偏弱时，延迟和丢包会先出现在电脑到路由器这一段。")
        return _finish("warn", "无线信号偏弱", detail, snapshot, "信号弱，并且已经出现丢包或抖动")

    interference = _interference(wifi, gateway, internet)
    if interference:
        detail = _join(
            _wifi_facts(wifi),
            "信号本身不弱。2.4 GHz 容易被隔壁网络干扰，这种干扰通常表现为延迟突然变大或连续几个包超时，网卡却仍显示已连接。",
            _jitter_clause(internet),
        )
        return _finish("warn", "疑似 2.4GHz 干扰", detail, snapshot, "2.4GHz 且信号不弱，但出现抖动或连续超时")

    if _jitter(internet) or _jitter(gateway):
        return _finish("warn", "延迟抖动", _jitter_sentence(gateway, internet), snapshot, "两边都有回复，但最长远高于平均")

    if gateway.loss_pct >= 5:
        detail = f"电脑到路由器丢包 {gateway.loss_pct}%。优先检查 Wi-Fi 或路由器。"
        return _finish("warn", "到路由器有丢包", detail, snapshot, "电脑到路由器丢包达到 5%")

    if internet.loss_pct >= 1:
        level = "bad" if internet.loss_pct >= 20 else "warn"
        detail = (
            f"路由器正常，到 223.5.5.5 丢包 {internet.loss_pct}%。"
            "丢包在路由器之外，偏向路由器或运营商。"
        )
        return _finish(level, "外网有丢包", detail, snapshot, "路由器正常，到 223.5.5.5 有丢包")

    if weak:
        detail = _join(_wifi_facts(wifi), "这次 ping 还正常，但信号余量不大，人一走远就容易抖。")
        return _finish("warn", "无线信号偏弱", detail, snapshot, "信号弱，这次 ping 仍正常")

    router = snapshot.lan_gateway
    internet_host = internet.host or "223.5.5.5"
    detail = (
        f"到路由器（{router}）\n"
        f"和到互联网（{internet_host}）都有回复，延迟稳定，没有丢包。"
    )
    return _finish("ok", "网络正常", detail, snapshot, "两边都通，延迟稳定，没有丢包")


def _finish(level: str, title: str, detail: str, snapshot: Snapshot, rule: str) -> Diagnosis:
    site_title, site_level, site_text, site_rule = _site_finding(snapshot)
    if site_title and title == "网络正常":
        title = site_title
        level = site_level
        rule = site_rule
    text = _join(detail, site_text)
    if snapshot.tun_likely and "隧道" not in text and "VPN" not in text:
        text = _join(
            text,
            f"默认路由当前是 {snapshot.default_gateway or 'VPN'}，不是路由器 {snapshot.lan_gateway}。",
        )
    return Diagnosis(level, title, text.strip(), rule)


def _site_finding(snapshot: Snapshot) -> tuple[str, str, str, str]:
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
            notes.append("到 223.5.5.5 的 ping 不经过域名，网页失败更像 DNS。")
        else:
            title, rule = "国内网站打不开", "地址能 ping 通，但国内网页没有回应"
            notes.append("ping 通国内地址，但网页没有回应，多半是 DNS 或网站本身。")
    elif domestic and link_up and any(not site.ok for site in domestic):
        failed = "、".join(site.name for site in domestic if not site.ok)
        notes.append(f"{failed} 这次没有打开。")

    if proxy.probe_foreign:
        if foreign and all(not site.ok for site in foreign) and any(site.ok for site in domestic):
            if not title:
                title, level, rule = "代理没有接通国外", "warn", "国内网页通，国外网页不通"
            notes.append("国内网站正常，国外网站失败。Clash 可能没连上节点，或规则没把国外流量送出去。")
        elif foreign and all(site.ok for site in foreign) and title == "":
            opened = [site for site in foreign if site.ms is not None]
            domestic_fast = domestic and all(site.ok and (site.ms or 0) < 400 for site in domestic)
            fastest = min((site.ms for site in opened), default=None)
            if opened and fastest is not None and fastest >= 1200 and domestic_fast:
                title, rule = "代理延迟偏高", "国外网站都能打开，但延迟都超过 1.2 秒"
                notes.append("国外网站能开，但延迟都很高，更像代理线路而不是家里的宽带。")
            else:
                slow_names = [site.name for site in opened if site.ms is not None and site.ms >= 1500]
                if slow_names and domestic_fast:
                    notes.append(f"{'、'.join(slow_names)} 这次特别慢，其他国外站点正常。")
                else:
                    notes.append("代理已开，国外网站有回应。")
    elif proxy.clash_running:
        notes.append("Clash 在运行，但系统代理没开，所以只测了国内网站。")

    return title, level, "".join(notes), rule


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
    parts = [f"连的是 {wifi.ssid or '无线'}"]
    if wifi.band:
        parts.append(wifi.band)
    if wifi.channel:
        parts.append(f"信道 {wifi.channel}")
    if wifi.width:
        parts.append(wifi.width)
    if wifi.radio:
        parts.append(wifi.radio)
    sentence = "、".join(parts)
    signal_bits = []
    if wifi.signal is not None:
        signal_bits.append(f"信号 {wifi.signal}%")
    if wifi.rssi is not None:
        signal_bits.append(f"RSSI {wifi.rssi}")
    if wifi.rx_mbps is not None:
        signal_bits.append(f"速率 {wifi.rx_mbps:g} Mbps")
    if signal_bits:
        sentence += "，" + "、".join(signal_bits)
    return sentence + "。"


def _jitter_clause(sample: PingSample) -> str:
    if sample.avg_ms is None or sample.max_ms is None:
        return ""
    return f"到 223.5.5.5 平均 {sample.avg_ms} ms、最长 {sample.max_ms} ms。"


def _jitter_sentence(gateway: PingSample, internet: PingSample) -> str:
    if _jitter(internet) and internet.avg_ms is not None and internet.max_ms is not None:
        return (
            "两边都有回复，但最长远高于平均。"
            f"到 223.5.5.5 平均 {internet.avg_ms} ms、最长 {internet.max_ms} ms，这就是抖动。"
        )
    if gateway.avg_ms is not None and gateway.max_ms is not None:
        return (
            "到互联网还算稳，但电脑到路由器的延迟突然变高："
            f"平均 {gateway.avg_ms} ms、最长 {gateway.max_ms} ms。更像无线这一段在抖。"
        )
    return "延迟突然变高，属于抖动。"


def _join(*parts: str) -> str:
    return "".join(part for part in parts if part)
