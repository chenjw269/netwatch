using NetWatch;

namespace NetWatch.Tests;

public class RuleTests
{
    private const string ChineseOk = """
        正在 Ping 192.168.1.1 具有 32 字节的数据:
        来自 192.168.1.1 的回复: 字节=32 时间=9ms TTL=64
        来自 192.168.1.1 的回复: 字节=32 时间=3ms TTL=64
        来自 192.168.1.1 的回复: 字节=32 时间<1ms TTL=64
        来自 192.168.1.1 的回复: 字节=32 时间=4ms TTL=64

        192.168.1.1 的 Ping 统计信息:
            数据包: 已发送 = 4，已接收 = 4，丢失 = 0 (0% 丢失)，
        往返行程的估计时间(以毫秒为单位):
            最短 = 1ms，最长 = 9ms，平均 = 4ms
        """;

    private const string ChineseLoss = """
        正在 Ping 223.5.5.5 具有 32 字节的数据:
        来自 223.5.5.5 的回复: 字节=32 时间=21ms TTL=54
        请求超时。
        请求超时。
        请求超时。

        223.5.5.5 的 Ping 统计信息:
            数据包: 已发送 = 4，已接收 = 1，丢失 = 3 (75% 丢失)，
        往返行程的估计时间(以毫秒为单位):
            最短 = 21ms，最长 = 21ms，平均 = 21ms
        """;

    private const string EnglishOk = """
        Pinging 1.1.1.1 with 32 bytes of data:
        Reply from 1.1.1.1: bytes=32 time=12ms TTL=54
        Request timed out.
        Reply from 1.1.1.1: bytes=32 time=18ms TTL=54
        Reply from 1.1.1.1: bytes=32 time=15ms TTL=54

        Ping statistics for 1.1.1.1:
            Packets: Sent = 4, Received = 3, Lost = 1 (25% loss),
        Approximate round trip times in milli-seconds:
            Minimum = 12ms, Maximum = 18ms, Average = 15ms
        """;

    private const string Route = """
                  0.0.0.0          0.0.0.0      192.168.1.1      192.168.1.4     50
                  0.0.0.0          0.0.0.0         10.8.0.1         10.8.0.2     80
        """;

    private const string Ipconfig = """
        无线局域网适配器 WLAN:

           媒体状态  . . . . . . . . . . . . : 媒体已断开连接
           IPv4 地址 . . . . . . . . . . . . : 192.168.0.8
           默认网关. . . . . . . . . . . . . : 192.168.0.1

        无线局域网适配器 WLAN:

           IPv4 地址 . . . . . . . . . . . . : 192.168.1.4
           默认网关. . . . . . . . . . . . . : fe80::1%12
                                               192.168.1.1
        """;

    private const string Wifi = """
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
        """;

    private static PingSample Ping(string host, int avg, int? maximum = null, int loss = 0, int sent = 4, int? streak = null)
    {
        maximum ??= avg;
        var received = sent - (int)Math.Round(sent * loss / 100.0);
        var rtts = Enumerable.Repeat<int?>(avg, received).Concat(Enumerable.Repeat<int?>(null, sent - received)).ToList();
        if (maximum != avg && received > 0)
            rtts[0] = maximum;
        return new PingSample
        {
            Host = host,
            Sent = sent,
            Received = received,
            LossPct = loss,
            MinMs = avg,
            MaxMs = received > 0 ? maximum : null,
            AvgMs = received > 0 ? avg : null,
            Rtts = rtts,
            MaxTimeoutStreak = streak ?? Parse.TimeoutStreak(rtts),
        };
    }

    private static Snapshot Snap() => new()
    {
        LanGateway = "192.168.1.1",
        DefaultGateway = "192.168.1.1",
        GatewayPing = Ping("192.168.1.1", 4),
        InternetPing = Ping("223.5.5.5", 21),
        Wifi = new WifiInfo
        {
            Ssid = "CMCC-affp",
            Band = "2.4 GHz",
            Channel = "3",
            Radio = "802.11n",
            Signal = 100,
            Rssi = -49,
            RxMbps = 144.4,
            Connected = true,
        },
        Proxy = new ProxyState(),
        CollectedAt = 1,
    };

    [Fact]
    public void ChinesePing()
    {
        var sample = Parse.ParsePing(ChineseOk, "192.168.1.1");
        Assert.Equal(4, sample.Sent);
        Assert.Equal(4, sample.Received);
        Assert.Equal(0, sample.LossPct);
        Assert.Equal(4, sample.AvgMs);
        Assert.Equal(9, sample.MaxMs);
        Assert.Equal(new int?[] { 9, 3, 1, 4 }, sample.Rtts);
        Assert.Equal(0, sample.MaxTimeoutStreak);
    }

    [Fact]
    public void ConsecutiveTimeouts()
    {
        var sample = Parse.ParsePing(ChineseLoss, "223.5.5.5");
        Assert.Equal(75, sample.LossPct);
        Assert.Equal(3, sample.MaxTimeoutStreak);
        Assert.Equal(21, sample.Rtts[0]);
        Assert.Equal(new int?[] { null, null, null }, sample.Rtts.Skip(1));
    }

    [Fact]
    public void EnglishPing()
    {
        var sample = Parse.ParsePing(EnglishOk, "1.1.1.1");
        Assert.Equal((4, 3, 25), (sample.Sent, sample.Received, sample.LossPct));
        Assert.Equal(1, sample.MaxTimeoutStreak);
        Assert.Equal(15, sample.AvgMs);
    }

    [Fact]
    public void RoutePicksLowestMetric()
    {
        Assert.Equal(("192.168.1.1", "192.168.1.4"), Parse.ParseDefaultRoute(Route));
    }

    [Fact]
    public void IpconfigSkipsLinkLocalAndDisconnected()
    {
        var adapters = Parse.ParseAdapters(Ipconfig);
        Assert.Equal("192.168.1.1", Parse.ChooseLanGateway(adapters, "192.168.1.4"));
    }

    [Fact]
    public void WifiUsesConnectedInterface()
    {
        var info = Parse.ParseWifi(Wifi);
        Assert.NotNull(info);
        Assert.True(info!.Connected);
        Assert.Equal("CMCC-affp", info.Ssid);
        Assert.Equal("2.4 GHz", info.Band);
        Assert.Equal("3", info.Channel);
        Assert.Equal("20 MHz", info.Width);
        Assert.Equal("802.11n", info.Radio);
        Assert.Equal(100, info.Signal);
        Assert.Equal(-49, info.Rssi);
        Assert.Equal(144.4, info.RxMbps);
    }

    [Fact]
    public void SiteAddressWithoutDns()
    {
        Assert.Equal("www.baidu.com", Collector.SiteHost("https://www.baidu.com"));
        Assert.Equal("", Collector.LookupIpv4(""));
        Assert.Equal("127.0.0.1", Collector.LookupIpv4("127.0.0.1"));
        Assert.Equal("", Collector.LookupIpv4("not a host"));
    }

    [Fact]
    public void ProxyString()
    {
        Assert.Equal("http://127.0.0.1:7897", ProxyText.NormalizeProxy("127.0.0.1:7897"));
        Assert.Equal("http://127.0.0.1:7890", ProxyText.NormalizeProxy("http=127.0.0.1:7890;https=127.0.0.1:7890"));
    }

    [Fact]
    public void BothDownIsLocal()
    {
        var snap = Snap();
        snap.GatewayPing = Ping("192.168.1.1", 1, loss: 100);
        snap.InternetPing = Ping("223.5.5.5", 1, loss: 100);
        var found = Diagnose.Run(snap);
        Assert.Equal("bad", found.Level);
        Assert.Equal("电脑→路由器 链路不通", found.Title);
        Assert.Contains("两段同时无响应", found.Detail);
    }

    [Fact]
    public void InternetOnlyIsIsp()
    {
        var snap = Snap();
        snap.InternetPing = Ping("223.5.5.5", 1, loss: 100);
        var found = Diagnose.Run(snap);
        Assert.Equal("路由器→互联网 链路不通", found.Title);
        Assert.Contains("持续收到回复", found.Detail);
    }

    [Fact]
    public void GatewayOnlyIsWifi()
    {
        var snap = Snap();
        snap.GatewayPing = Ping("192.168.1.1", 8, loss: 75);
        var found = Diagnose.Run(snap);
        Assert.Equal("bad", found.Level);
        Assert.Equal("电脑→路由器 链路不通", found.Title);
    }

    [Fact]
    public void SilentGatewayWithInternetIsForwarding()
    {
        var snap = Snap();
        snap.GatewayPing = Ping("192.168.1.1", 1, loss: 100);
        var found = Diagnose.Run(snap);
        Assert.Equal("ok", found.Level);
        Assert.Equal("网络正常", found.Title);
        Assert.Contains("不回应 ping 测试请求", found.Detail);
        Assert.Contains("800", found.Detail);
        Assert.Contains("未测得延迟", found.Detail);
        Assert.Contains("1. ", found.Detail);
        Assert.Contains("\n2. ", found.Detail);
        Assert.Contains("\n3. ", found.Detail);
        Assert.Contains("路由器当前可正常转发，但不回应 ping 测试请求。", found.Detail);
    }

    [Fact]
    public void Jitter()
    {
        var snap = Snap();
        snap.Wifi = new WifiInfo { Ssid = "home", Band = "5 GHz", Signal = 90, Rssi = -50, Connected = true };
        snap.InternetPing = Ping("223.5.5.5", 20, maximum: 180);
        var found = Diagnose.Run(snap);
        Assert.Equal("路由器→互联网 链路抖动", found.Title);
        Assert.Contains("最长", found.Detail);
    }

    [Fact]
    public void Interference24()
    {
        var snap = Snap();
        snap.InternetPing = Ping("223.5.5.5", 20, maximum: 180);
        var found = Diagnose.Run(snap);
        Assert.Equal("电脑→路由器 2.4GHz 干扰", found.Title);
        Assert.Contains("信号强度并不弱", found.Detail);
        Assert.Contains("CMCC-affp", found.Detail);
    }

    [Fact]
    public void Stable24GhzStaysOk()
    {
        var found = Diagnose.Run(Snap());
        Assert.Equal("网络正常", found.Title);
        Assert.Equal("ok", found.Level);
        Assert.Equal("两边都通，延迟稳定，没有丢包", found.Rule);
        Assert.Contains("到路由器（192.168.1.1）", found.Detail);
        Assert.Contains("到互联网（223.5.5.5）", found.Detail);
        Assert.Contains("未出现丢包", found.Detail);
        Assert.Contains("\n2. ", found.Detail);
    }

    [Fact]
    public void InternetLoss()
    {
        var snap = Snap();
        snap.InternetPing = Ping("223.5.5.5", 22, loss: 25);
        var found = Diagnose.Run(snap);
        Assert.Equal("路由器→互联网 链路丢包", found.Title);
        Assert.Contains("25%", found.Detail);
    }

    [Fact]
    public void WeakSignal()
    {
        var snap = Snap();
        snap.Wifi = new WifiInfo { Ssid = "x", Band = "2.4 GHz", Signal = 30, Rssi = -82, Connected = true };
        var found = Diagnose.Run(snap);
        Assert.Equal("电脑→路由器 信号偏弱", found.Title);
    }

    [Fact]
    public void NoGateway()
    {
        var snap = Snap();
        snap.LanGateway = "";
        var found = Diagnose.Run(snap);
        Assert.Equal("电脑→路由器 未连接", found.Title);
    }

    [Fact]
    public void ProxyOkIsItsOwnPoint()
    {
        var snap = Snap();
        snap.GatewayPing = Ping("192.168.102.1", 1, loss: 100);
        snap.InternetPing = Ping("223.5.5.5", 18);
        snap.Sites =
        [
            new SiteProbe { Name = "百度", Ok = true, Ms = 40 },
            new SiteProbe { Name = "Google", Ok = true, Ms = 180, Foreign = true },
            new SiteProbe { Name = "Cloudflare", Ok = true, Ms = 220, Foreign = true },
        ];
        snap.Proxy = new ProxyState { SystemEnabled = true, Server = "127.0.0.1:7890" };
        var found = Diagnose.Run(snap);
        Assert.Equal("网络正常", found.Title);
        Assert.Contains("\n4. 网络代理已开启，国外网站可正常访问。", found.Detail);
        Assert.DoesNotContain("国外网站有回应", found.Detail);
    }

    [Fact]
    public void OneSlowForeignSiteDoesNotBlameTheProxy()
    {
        var snap = Snap();
        snap.Sites =
        [
            new SiteProbe { Name = "百度", Ok = true, Ms = 40 },
            new SiteProbe { Name = "腾讯", Ok = true, Ms = 50 },
            new SiteProbe { Name = "Google", Ok = true, Ms = 4800, Foreign = true },
            new SiteProbe { Name = "Cloudflare", Ok = true, Ms = 250, Foreign = true },
        ];
        snap.Proxy = new ProxyState { SystemEnabled = true, Server = "127.0.0.1:7890" };
        var found = Diagnose.Run(snap);
        Assert.Equal("网络正常", found.Title);
        Assert.Contains("Google", found.Detail);
        Assert.Contains("其余国外站点访问正常", found.Detail);
    }

    [Fact]
    public void AllForeignSitesSlow()
    {
        var snap = Snap();
        snap.Sites =
        [
            new SiteProbe { Name = "百度", Ok = true, Ms = 40 },
            new SiteProbe { Name = "腾讯", Ok = true, Ms = 50 },
            new SiteProbe { Name = "Google", Ok = true, Ms = 2000, Foreign = true },
            new SiteProbe { Name = "Cloudflare", Ok = true, Ms = 1800, Foreign = true },
        ];
        snap.Proxy = new ProxyState { SystemEnabled = true, Server = "127.0.0.1:7890" };
        var found = Diagnose.Run(snap);
        Assert.Equal("代理延迟偏高", found.Title);
    }

    [Fact]
    public void ForeignFailureOnlyWhenProxyOn()
    {
        var sites = new List<SiteProbe>
        {
            new() { Name = "百度", Ok = true, Ms = 40 },
            new() { Name = "腾讯", Ok = true, Ms = 50 },
            new() { Name = "Google", Ok = false, Error = "超时", Foreign = true },
            new() { Name = "Wikipedia", Ok = false, Error = "超时", Foreign = true },
        };
        var off = Snap();
        off.Sites = sites;
        Assert.Equal("网络正常", Diagnose.Run(off).Title);
        var on = Snap();
        on.Sites = sites.Select(site => new SiteProbe
        {
            Name = site.Name, Ok = site.Ok, Ms = site.Ms, Error = site.Error, Foreign = site.Foreign,
        }).ToList();
        on.Proxy = new ProxyState { SystemEnabled = true, Server = "127.0.0.1:7897" };
        Assert.Equal("代理网络不通", Diagnose.Run(on).Title);
    }

    [Fact]
    public void DnsWhenPingOk()
    {
        var snap = Snap();
        snap.Sites =
        [
            new SiteProbe { Name = "百度", Ok = false, Error = "DNS 解析失败" },
            new SiteProbe { Name = "腾讯", Ok = false, Error = "DNS 解析失败" },
        ];
        Assert.Equal("DNS 解析失败", Diagnose.Run(snap).Title);
    }

    [Fact]
    public void LinkDownIsNotReplacedBySites()
    {
        var snap = Snap();
        snap.InternetPing = Ping("223.5.5.5", 1, loss: 100);
        snap.Sites = [new SiteProbe { Name = "百度", Ok = false, Error = "超时" }];
        Assert.Equal("路由器→互联网 链路不通", Diagnose.Run(snap).Title);
    }

    [Fact]
    public void TunDoesNotBlameIsp()
    {
        var snap = Snap();
        snap.DefaultGateway = "10.8.0.1";
        snap.TunLikely = true;
        snap.InternetPing = Ping("223.5.5.5", 1, loss: 100);
        var found = Diagnose.Run(snap);
        Assert.Equal("路由器→互联网 没有回应", found.Title);
        Assert.Contains("隧道", found.Detail);
    }

    [Fact]
    public void DefaultIsAFewMinutes()
    {
        Assert.Equal(180, Config.DefaultIntervalSec);
        Assert.Equal(180, Config.NormalizeInterval(null));
        Assert.Equal(180, Config.NormalizeInterval(14));
        Assert.Equal(180, Config.NormalizeInterval(true));
        Assert.Equal(300, Config.NormalizeInterval(300));
        Assert.Equal("3 分钟", Config.IntervalLabel(180));
    }
}
