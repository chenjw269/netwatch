from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from netwatch.config import DEFAULT_INTERVAL_SEC, interval_label, normalize_interval
from netwatch.diagnose import diagnose
from netwatch.system import lookup_ipv4, site_host
from netwatch.models import PingSample, ProxyState, SiteProbe, Snapshot, WifiInfo, normalize_proxy
from netwatch.parse import (
    choose_lan_gateway,
    parse_adapters,
    parse_default_route,
    parse_ping,
    parse_wifi,
    timeout_streak,
)

CHINESE_OK = """
正在 Ping 192.168.1.1 具有 32 字节的数据:
来自 192.168.1.1 的回复: 字节=32 时间=9ms TTL=64
来自 192.168.1.1 的回复: 字节=32 时间=3ms TTL=64
来自 192.168.1.1 的回复: 字节=32 时间<1ms TTL=64
来自 192.168.1.1 的回复: 字节=32 时间=4ms TTL=64

192.168.1.1 的 Ping 统计信息:
    数据包: 已发送 = 4，已接收 = 4，丢失 = 0 (0% 丢失)，
往返行程的估计时间(以毫秒为单位):
    最短 = 1ms，最长 = 9ms，平均 = 4ms
"""

CHINESE_LOSS = """
正在 Ping 223.5.5.5 具有 32 字节的数据:
来自 223.5.5.5 的回复: 字节=32 时间=21ms TTL=54
请求超时。
请求超时。
请求超时。

223.5.5.5 的 Ping 统计信息:
    数据包: 已发送 = 4，已接收 = 1，丢失 = 3 (75% 丢失)，
往返行程的估计时间(以毫秒为单位):
    最短 = 21ms，最长 = 21ms，平均 = 21ms
"""

ENGLISH_OK = """
Pinging 1.1.1.1 with 32 bytes of data:
Reply from 1.1.1.1: bytes=32 time=12ms TTL=54
Request timed out.
Reply from 1.1.1.1: bytes=32 time=18ms TTL=54
Reply from 1.1.1.1: bytes=32 time=15ms TTL=54

Ping statistics for 1.1.1.1:
    Packets: Sent = 4, Received = 3, Lost = 1 (25% loss),
Approximate round trip times in milli-seconds:
    Minimum = 12ms, Maximum = 18ms, Average = 15ms
"""

ROUTE = """
          0.0.0.0          0.0.0.0      192.168.1.1      192.168.1.4     50
          0.0.0.0          0.0.0.0         10.8.0.1         10.8.0.2     80
"""

IPCONFIG = """
无线局域网适配器 WLAN:

   媒体状态  . . . . . . . . . . . . : 媒体已断开连接
   IPv4 地址 . . . . . . . . . . . . : 192.168.0.8
   默认网关. . . . . . . . . . . . . : 192.168.0.1

无线局域网适配器 WLAN:

   IPv4 地址 . . . . . . . . . . . . : 192.168.1.4
   默认网关. . . . . . . . . . . . . : fe80::1%12
                                       192.168.1.1
"""

WIFI = """
    名称                   : WLAN
    状态                  : 已连接
    SSID                   : CMCC-affp
    波段                   : 2.4 GHz
    通道                : 3
    通道宽度               : 20 MHz
    无线电类型             : 802.11n
    接收速率(Mbps)         : 144.4
    信号                   : 100%
    Rssi                   : -49

    名称                   : WLAN 3
    状态                  : 已断开连接
    SSID                   : other
"""


def ping(
    host: str,
    avg: int,
    *,
    maximum: int | None = None,
    loss: int = 0,
    sent: int = 4,
    streak: int | None = None,
) -> PingSample:
    maximum = avg if maximum is None else maximum
    received = sent - int(round(sent * loss / 100))
    rtts: list[int | None] = [avg] * received + [None] * (sent - received)
    if maximum != avg and received:
        rtts[0] = maximum
    return PingSample(
        host=host,
        sent=sent,
        received=received,
        loss_pct=loss,
        min_ms=avg,
        max_ms=maximum if received else None,
        avg_ms=avg if received else None,
        rtts=rtts,
        max_timeout_streak=timeout_streak(rtts) if streak is None else streak,
    )


def snap(**kwargs) -> Snapshot:
    base = Snapshot(
        lan_gateway="192.168.1.1",
        default_gateway="192.168.1.1",
        gateway_ping=ping("192.168.1.1", 4),
        internet_ping=ping("223.5.5.5", 21),
        wifi=WifiInfo(
            ssid="CMCC-affp",
            band="2.4 GHz",
            channel="3",
            radio="802.11n",
            signal=100,
            rssi=-49,
            rx_mbps=144.4,
            connected=True,
        ),
        proxy=ProxyState(),
        collected_at=1,
    )
    for key, value in kwargs.items():
        setattr(base, key, value)
    return base


class ParseTests(unittest.TestCase):
    def test_chinese_ping(self) -> None:
        sample = parse_ping(CHINESE_OK, "192.168.1.1")
        self.assertEqual(sample.sent, 4)
        self.assertEqual(sample.received, 4)
        self.assertEqual(sample.loss_pct, 0)
        self.assertEqual(sample.avg_ms, 4)
        self.assertEqual(sample.max_ms, 9)
        self.assertEqual(sample.rtts, [9, 3, 1, 4])
        self.assertEqual(sample.max_timeout_streak, 0)

    def test_consecutive_timeouts(self) -> None:
        sample = parse_ping(CHINESE_LOSS, "223.5.5.5")
        self.assertEqual(sample.loss_pct, 75)
        self.assertEqual(sample.max_timeout_streak, 3)
        self.assertEqual(sample.rtts[0], 21)
        self.assertEqual(sample.rtts[1:], [None, None, None])

    def test_english_ping(self) -> None:
        sample = parse_ping(ENGLISH_OK, "1.1.1.1")
        self.assertEqual((sample.sent, sample.received, sample.loss_pct), (4, 3, 25))
        self.assertEqual(sample.max_timeout_streak, 1)
        self.assertEqual(sample.avg_ms, 15)

    def test_route_picks_lowest_metric(self) -> None:
        self.assertEqual(parse_default_route(ROUTE), ("192.168.1.1", "192.168.1.4"))

    def test_ipconfig_skips_link_local_and_disconnected(self) -> None:
        adapters = parse_adapters(IPCONFIG)
        self.assertEqual(choose_lan_gateway(adapters, "192.168.1.4"), "192.168.1.1")

    def test_wifi_uses_connected_interface(self) -> None:
        info = parse_wifi(WIFI)
        self.assertIsNotNone(info)
        assert info is not None
        self.assertTrue(info.connected)
        self.assertEqual(info.ssid, "CMCC-affp")
        self.assertEqual(info.band, "2.4 GHz")
        self.assertEqual(info.channel, "3")
        self.assertEqual(info.width, "20 MHz")
        self.assertEqual(info.radio, "802.11n")
        self.assertEqual(info.signal, 100)
        self.assertEqual(info.rssi, -49)
        self.assertEqual(info.rx_mbps, 144.4)

    def test_site_address_without_dns(self) -> None:
        self.assertEqual(site_host("https://www.baidu.com"), "www.baidu.com")
        self.assertEqual(lookup_ipv4(""), "")
        self.assertEqual(lookup_ipv4("127.0.0.1"), "127.0.0.1")
        self.assertEqual(lookup_ipv4("not a host"), "")

    def test_proxy_string(self) -> None:
        self.assertEqual(normalize_proxy("127.0.0.1:7897"), "http://127.0.0.1:7897")
        self.assertEqual(
            normalize_proxy("http=127.0.0.1:7890;https=127.0.0.1:7890"),
            "http://127.0.0.1:7890",
        )


class DiagnoseTests(unittest.TestCase):
    def test_both_down_is_local(self) -> None:
        found = diagnose(
            snap(
                gateway_ping=ping("192.168.1.1", 1, loss=100),
                internet_ping=ping("223.5.5.5", 1, loss=100),
            )
        )
        self.assertEqual(found.level, "bad")
        self.assertEqual(found.title, "本地无线或路由器")
        self.assertIn("两边同时超时", found.detail)

    def test_internet_only_is_isp(self) -> None:
        found = diagnose(snap(internet_ping=ping("223.5.5.5", 1, loss=100)))
        self.assertEqual(found.title, "路由器或运营商")
        self.assertIn("网关一直通", found.detail)

    def test_gateway_only_is_wifi(self) -> None:
        found = diagnose(snap(gateway_ping=ping("192.168.1.1", 1, loss=100)))
        self.assertEqual(found.title, "Wi-Fi 或路由器")

    def test_jitter(self) -> None:
        found = diagnose(
            snap(
                wifi=WifiInfo(ssid="home", band="5 GHz", signal=90, rssi=-50, connected=True),
                internet_ping=ping("223.5.5.5", 20, maximum=180),
            )
        )
        self.assertEqual(found.title, "延迟抖动")
        self.assertIn("最长", found.detail)

    def test_24ghz_interference(self) -> None:
        found = diagnose(snap(internet_ping=ping("223.5.5.5", 20, maximum=180)))
        self.assertEqual(found.title, "疑似 2.4GHz 干扰")
        self.assertIn("信号本身不弱", found.detail)
        self.assertIn("CMCC-affp", found.detail)

    def test_stable_24ghz_stays_ok(self) -> None:
        found = diagnose(snap())
        self.assertEqual(found.title, "网络正常")
        self.assertEqual(found.level, "ok")
        self.assertEqual(found.rule, "两边都通，延迟稳定，没有丢包")
        self.assertIn("到路由器（192.168.1.1）", found.detail)
        self.assertIn("和到互联网（223.5.5.5）都有回复，延迟稳定，没有丢包。", found.detail)

    def test_internet_loss(self) -> None:
        found = diagnose(snap(internet_ping=ping("223.5.5.5", 22, loss=25)))
        self.assertEqual(found.title, "外网有丢包")
        self.assertIn("25%", found.detail)

    def test_weak_signal(self) -> None:
        found = diagnose(
            snap(wifi=WifiInfo(ssid="x", band="2.4 GHz", signal=30, rssi=-82, connected=True))
        )
        self.assertEqual(found.title, "无线信号偏弱")

    def test_no_gateway(self) -> None:
        found = diagnose(snap(lan_gateway=""))
        self.assertEqual(found.title, "没有找到路由器")

    def test_one_slow_foreign_site_does_not_blame_the_proxy(self) -> None:
        sites = [
            SiteProbe("百度", True, 40, "", False),
            SiteProbe("腾讯", True, 50, "", False),
            SiteProbe("Google", True, 4800, "", True),
            SiteProbe("Cloudflare", True, 250, "", True),
        ]
        found = diagnose(
            snap(sites=sites, proxy=ProxyState(system_enabled=True, server="127.0.0.1:7890"))
        )
        self.assertEqual(found.title, "网络正常")
        self.assertIn("Google", found.detail)

    def test_all_foreign_sites_slow(self) -> None:
        sites = [
            SiteProbe("百度", True, 40, "", False),
            SiteProbe("腾讯", True, 50, "", False),
            SiteProbe("Google", True, 2000, "", True),
            SiteProbe("Cloudflare", True, 1800, "", True),
        ]
        found = diagnose(
            snap(sites=sites, proxy=ProxyState(system_enabled=True, server="127.0.0.1:7890"))
        )
        self.assertEqual(found.title, "代理延迟偏高")

    def test_foreign_failure_only_when_proxy_on(self) -> None:
        sites = [
            SiteProbe("百度", True, 40, "", False),
            SiteProbe("腾讯", True, 50, "", False),
            SiteProbe("Google", False, None, "超时", True),
            SiteProbe("Wikipedia", False, None, "超时", True),
        ]
        off = diagnose(snap(sites=sites, proxy=ProxyState()))
        self.assertEqual(off.title, "网络正常")
        on = diagnose(snap(sites=sites, proxy=ProxyState(system_enabled=True, server="127.0.0.1:7897")))
        self.assertEqual(on.title, "代理没有接通国外")

    def test_dns_when_ping_ok(self) -> None:
        sites = [
            SiteProbe("百度", False, None, "DNS 解析失败", False),
            SiteProbe("腾讯", False, None, "DNS 解析失败", False),
        ]
        found = diagnose(snap(sites=sites))
        self.assertEqual(found.title, "DNS 解析失败")

    def test_link_down_is_not_replaced_by_sites(self) -> None:
        sites = [SiteProbe("百度", False, None, "超时", False)]
        found = diagnose(snap(internet_ping=ping("223.5.5.5", 1, loss=100), sites=sites))
        self.assertEqual(found.title, "路由器或运营商")

    def test_tun_does_not_blame_isp(self) -> None:
        found = diagnose(
            snap(
                default_gateway="10.8.0.1",
                tun_likely=True,
                internet_ping=ping("223.5.5.5", 1, loss=100),
            )
        )
        self.assertNotEqual(found.title, "路由器或运营商")
        self.assertIn("隧道", found.detail)


class TestInterval(unittest.TestCase):
    def test_default_is_a_few_minutes(self) -> None:
        self.assertEqual(DEFAULT_INTERVAL_SEC, 180)
        self.assertEqual(normalize_interval(None), 180)
        self.assertEqual(normalize_interval(14), 180)
        self.assertEqual(normalize_interval(True), 180)
        self.assertEqual(normalize_interval(300), 300)
        self.assertEqual(interval_label(180), "3 分钟")


if __name__ == "__main__":
    unittest.main()
