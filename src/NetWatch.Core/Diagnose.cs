namespace NetWatch;

public static class Diagnose
{
    private const int DownLoss = 60;
    private const int DownStreak = 3;
    private const int JitterMaxMs = 100;
    private const int JitterRatio = 3;
    private const int WeakRssi = -75;
    private const int WeakSignal = 45;
    private const int StrongRssi = -65;
    private const int StrongSignal = 70;

    public static Diagnosis Run(Snapshot snapshot)
    {
        if (!string.IsNullOrEmpty(snapshot.Error))
            return new Diagnosis("bad", "检测失败", snapshot.Error, "检测过程出错");

        var gateway = snapshot.GatewayPing;
        var internet = snapshot.InternetPing;
        var wifi = snapshot.Wifi;

        if (string.IsNullOrEmpty(snapshot.LanGateway))
        {
            return new Diagnosis(
                "bad",
                "电脑→路由器 未连接",
                Points(["未检测到局域网网关。", "电脑当前可能未连接无线网络或网线。"]),
                "没有局域网网关");
        }

        if (gateway.Sent == 0 && internet.Sent == 0)
        {
            return new Diagnosis(
                "bad",
                "Ping 没有执行",
                Points([FirstNonEmpty(gateway.Error, internet.Error, "本轮连通性测试未能执行。")]),
                "ping 没有返回结果");
        }

        var gatewayDown = SegmentDown(gateway);
        var internetDown = SegmentDown(internet);

        if (snapshot.TunLikely && internetDown && !gatewayDown)
        {
            var points = new List<string>
            {
                $"到路由器 {snapshot.LanGateway} 的测试已收到回复。",
                "到 223.5.5.5 的测试未收到回复。",
                $"当前默认路由指向 {Or(snapshot.DefaultGateway, "VPN")}，测试流量可能进入 VPN 隧道，不能据此判断路由器或运营商故障。",
            };
            return Finish("warn", "路由器→互联网 没有回应", points, snapshot, "默认路由在 VPN 上，不能据此判断路由器");
        }

        if (gatewayDown && internetDown)
        {
            return Finish("bad", "电脑→路由器 链路不通",
            [
                "到路由器与到 223.5.5.5 的测试均超时。",
                "两段同时无响应，故障位于电脑到路由器这一段。",
                WifiFacts(wifi),
            ], snapshot, "电脑到路由器、路由器到互联网同时超时");
        }

        var silent = RouterIgnoresPing(gateway, internet);
        var seenGateway = silent ? new PingSample { Host = gateway.Host } : gateway;

        if (gatewayDown && !silent)
        {
            List<string> points = gateway.Received <= 0
                ?
                [
                    $"到路由器的 ping 没有回应。所有测试包均超时，未测得延迟。每个测试包等待 {Config.PingWaitMs} ms。",
                    "到互联网的测试亦未收到正常回复。",
                    "故障位于无线网络或路由器。",
                    WifiFacts(wifi),
                ]
                :
                [
                    "到路由器的测试出现连续超时。",
                    "故障位于无线网络或路由器。",
                    WifiFacts(wifi),
                ];
            return Finish("bad", "电脑→路由器 链路不通", points, snapshot, "只有电脑到路由器这一段超时");
        }

        if (internetDown)
        {
            List<string> points = gateway.LossPct == 0 && gateway.MaxTimeoutStreak == 0 && gateway.Replied
                ?
                [
                    "到路由器的测试持续收到回复，未出现丢包或连续超时。",
                    "到 223.5.5.5 的测试超时。",
                    "故障位于路由器到互联网这一段，可能与路由器或运营商有关。",
                ]
                :
                [
                    "到路由器的测试仍有回复。",
                    "到 223.5.5.5 的测试基本超时。",
                    "主要故障位于路由器到互联网这一段。",
                ];
            return Finish("bad", "路由器→互联网 链路不通", points, snapshot, "路由器通，只有到 223.5.5.5 超时");
        }

        var weak = SignalWeak(wifi);
        var symptoms = Jitter(seenGateway)
            || Jitter(internet)
            || seenGateway.LossPct >= 5
            || internet.LossPct >= 5
            || seenGateway.MaxTimeoutStreak >= 2
            || internet.MaxTimeoutStreak >= 2;
        if (weak && symptoms)
        {
            return Finish("warn", "电脑→路由器 信号偏弱",
            [
                WifiFacts(wifi),
                "信号偏弱时，时延升高和丢包会首先出现在电脑到路由器这一段。",
            ], snapshot, "信号弱，并且已经出现丢包或抖动");
        }

        if (Interference(wifi, seenGateway, internet))
        {
            return Finish("warn", "电脑→路由器 2.4GHz 干扰",
            [
                WifiFacts(wifi),
                "当前信号强度并不弱。2.4 GHz 频段易受邻近网络干扰，通常表现为时延突然升高或连续数个测试包超时，而网卡仍显示已连接。",
                JitterClause(internet),
            ], snapshot, "2.4GHz 且信号不弱，但出现抖动或连续超时");
        }

        if (Jitter(internet) || Jitter(seenGateway))
        {
            string title;
            string rule;
            if (Jitter(internet))
            {
                title = "路由器→互联网 链路抖动";
                rule = silent ? "路由器不回答 ping，到互联网出现抖动" : "到互联网的最长远高于平均";
            }
            else
            {
                title = "电脑→路由器 链路抖动";
                rule = "电脑到路由器的最长远高于平均";
            }
            return Finish("warn", title, JitterPoints(gateway, internet), snapshot, rule);
        }

        if (seenGateway.LossPct >= 5)
        {
            return Finish("warn", "电脑→路由器 链路丢包",
            [
                $"电脑到路由器的丢包率为 {gateway.LossPct}%。",
                "建议优先检查无线网络或路由器。",
            ], snapshot, "电脑到路由器丢包达到 5%");
        }

        if (internet.LossPct >= 1)
        {
            var level = internet.LossPct >= 20 ? "bad" : "warn";
            return Finish(level, "路由器→互联网 链路丢包",
            [
                "到路由器的测试正常。",
                $"到 223.5.5.5 的丢包率为 {internet.LossPct}%。",
                "丢包发生在路由器之外，更可能与路由器出口或运营商有关。",
            ], snapshot, "路由器正常，到 223.5.5.5 有丢包");
        }

        if (weak)
        {
            return Finish("warn", "电脑→路由器 信号偏弱",
            [
                WifiFacts(wifi),
                "本轮连通性测试结果正常，但无线信号余量不足，距离路由器较远时容易出现波动。",
            ], snapshot, "信号弱，这次 ping 仍正常");
        }

        var router = snapshot.LanGateway;
        var internetHost = string.IsNullOrEmpty(internet.Host) ? "223.5.5.5" : internet.Host;
        if (silent)
        {
            return Finish("ok", "网络正常",
            [
                $"到路由器 {router} 的 ping 没有回应。所有测试包均超时，未测得延迟。每个测试包等待 {Config.PingWaitMs} ms。",
                InternetReply(internetHost, internet),
                "路由器当前可正常转发，但不回应 ping 测试请求。",
            ], snapshot, "路由器不回答 ping，到互联网有回复");
        }
        return Finish("ok", "网络正常",
        [
            $"到路由器（{router}）的测试已收到回复。",
            $"到互联网（{internetHost}）的测试已收到回复。",
            "两段时延稳定，未出现丢包。",
        ], snapshot, "两边都通，延迟稳定，没有丢包");
    }

    private static Diagnosis Finish(string level, string title, List<string> points, Snapshot snapshot, string rule)
    {
        var (siteTitle, siteLevel, siteNotes, siteRule) = SiteFinding(snapshot);
        if (!string.IsNullOrEmpty(siteTitle) && title == "网络正常")
        {
            title = siteTitle;
            level = siteLevel;
            rule = siteRule;
        }
        var items = points.Where(item => !string.IsNullOrEmpty(item)).Concat(siteNotes).ToList();
        if (snapshot.TunLikely && !items.Any(item => item.Contains("隧道") || item.Contains("VPN")))
        {
            items.Add($"当前默认路由为 {Or(snapshot.DefaultGateway, "VPN")}，并非局域网路由器 {snapshot.LanGateway}。");
        }
        return new Diagnosis(level, title, Points(items), rule);
    }

    private static (string Title, string Level, List<string> Notes, string Rule) SiteFinding(Snapshot snapshot)
    {
        var domestic = snapshot.Sites.Where(site => !site.Foreign).ToList();
        var foreign = snapshot.Sites.Where(site => site.Foreign).ToList();
        var proxy = snapshot.Proxy;
        var notes = new List<string>();
        var title = "";
        var level = "warn";
        var rule = "";

        var linkUp = snapshot.InternetPing.Replied && snapshot.InternetPing.LossPct < 20;
        if (domestic.Count > 0 && domestic.All(site => !site.Ok) && linkUp)
        {
            if (domestic.All(site => site.Error.StartsWith("DNS", StringComparison.Ordinal)))
            {
                title = "DNS 解析失败";
                level = "bad";
                rule = "地址能 ping 通，但域名解析失败";
                notes.Add("到 223.5.5.5 的连通测试不经过域名解析。");
                notes.Add("国内网站访问失败，更符合 DNS 解析异常。");
            }
            else
            {
                title = "国内网站不通";
                rule = "地址能 ping 通，但国内网页没有回应";
                notes.Add("国内地址的连通测试已收到回复，但网页访问没有响应。");
                notes.Add("可能与 DNS 解析或目标网站本身有关。");
            }
        }
        else if (domestic.Count > 0 && linkUp && domestic.Any(site => !site.Ok))
        {
            var failed = string.Join("、", domestic.Where(site => !site.Ok).Select(site => site.Name));
            notes.Add($"{failed} 本次访问未成功。");
        }

        if (proxy.ProbeForeign)
        {
            if (foreign.Count > 0 && foreign.All(site => !site.Ok) && domestic.Any(site => site.Ok))
            {
                if (title.Length == 0)
                {
                    title = "代理网络不通";
                    level = "warn";
                    rule = "国内网页通，国外网页不通";
                }
                notes.Add("国内网站访问正常，国外网站访问失败。");
                notes.Add("代理可能未成功连接节点，或规则未将国外流量转发至代理。");
            }
            else if (foreign.Count > 0 && foreign.All(site => site.Ok) && title.Length == 0)
            {
                var opened = foreign.Where(site => site.Ms is not null).ToList();
                var domesticFast = domestic.Count > 0 && domestic.All(site => site.Ok && (site.Ms ?? 0) < 400);
                int? fastest = opened.Count == 0 ? null : opened.Min(site => site.Ms);
                if (opened.Count > 0 && fastest is >= 1200 && domesticFast)
                {
                    title = "代理延迟偏高";
                    rule = "国外网站都能打开，但延迟都超过 1.2 秒";
                    notes.Add("国外网站可以打开，但时延均较高。");
                    notes.Add("更符合代理线路时延偏高，而非本地宽带故障。");
                }
                else
                {
                    var slowNames = opened.Where(site => site.Ms is >= 1500).Select(site => site.Name).ToList();
                    if (slowNames.Count > 0 && domesticFast)
                        notes.Add($"{string.Join("、", slowNames)} 本次访问时延明显偏高，其余国外站点访问正常。");
                    else
                        notes.Add("网络代理已开启，国外网站可正常访问。");
                }
            }
        }
        else if (proxy.ClashRunning)
        {
            notes.Add("Clash 正在运行，但系统代理未开启，因此本轮仅测试了国内网站。");
        }

        return (title, level, notes, rule);
    }

    private static bool RouterIgnoresPing(PingSample gateway, PingSample internet) =>
        gateway.Sent > 0 && gateway.Received <= 0 && internet.Replied && !SegmentDown(internet);

    private static bool SegmentDown(PingSample sample)
    {
        if (sample.Sent <= 0)
            return false;
        if (sample.Received <= 0 || sample.LossPct >= DownLoss)
            return true;
        return sample.MaxTimeoutStreak >= DownStreak;
    }

    private static bool Jitter(PingSample sample)
    {
        if (sample.AvgMs is null || sample.MaxMs is null || sample.Received <= 0)
            return false;
        return sample.MaxMs >= JitterMaxMs && sample.MaxMs >= sample.AvgMs * JitterRatio;
    }

    private static bool SignalWeak(WifiInfo? wifi)
    {
        if (wifi is not { Connected: true })
            return false;
        if (wifi.Rssi is not null && wifi.Rssi <= WeakRssi)
            return true;
        return wifi.Signal is not null && wifi.Signal < WeakSignal;
    }

    private static bool SignalStrong(WifiInfo? wifi)
    {
        if (wifi is not { Connected: true })
            return false;
        if (wifi.Signal is not null && wifi.Signal >= StrongSignal)
            return true;
        return wifi.Rssi is not null && wifi.Rssi >= StrongRssi;
    }

    private static bool Interference(WifiInfo? wifi, PingSample gateway, PingSample internet)
    {
        if (wifi is not { Connected: true } || !(wifi.Band ?? "").Contains("2.4"))
            return false;
        if (!SignalStrong(wifi))
            return false;
        var burst = gateway.MaxTimeoutStreak >= 2 || internet.MaxTimeoutStreak >= 2;
        return Jitter(internet) || Jitter(gateway) || burst;
    }

    public static string WifiFacts(WifiInfo? wifi)
    {
        if (wifi is not { Connected: true })
            return "";
        var parts = new List<string> { $"当前连接无线网络 {Or(wifi.Ssid, "未命名网络")}" };
        if (!string.IsNullOrEmpty(wifi.Band))
            parts.Add($"频段 {wifi.Band}");
        if (!string.IsNullOrEmpty(wifi.Channel))
            parts.Add($"信道 {wifi.Channel}");
        if (!string.IsNullOrEmpty(wifi.Width))
            parts.Add(wifi.Width);
        if (!string.IsNullOrEmpty(wifi.Radio))
            parts.Add(wifi.Radio);
        var sentence = string.Join("，", parts);
        var signalBits = new List<string>();
        if (wifi.Signal is not null)
            signalBits.Add($"信号 {wifi.Signal}%");
        if (wifi.Rssi is not null)
            signalBits.Add($"RSSI {wifi.Rssi}");
        if (wifi.RxMbps is not null)
            signalBits.Add($"协商速率 {wifi.RxMbps.Value:g} Mbps");
        if (signalBits.Count > 0)
            sentence += "，" + string.Join("，", signalBits);
        return sentence + "。";
    }

    private static string InternetReply(string host, PingSample sample)
    {
        var bits = new List<string> { $"到 {host} 的测试已收到回复" };
        if (sample.AvgMs is not null)
            bits.Add($"平均时延 {sample.AvgMs} ms");
        if (sample.MaxMs is not null && sample.MaxMs != sample.AvgMs)
            bits.Add($"最长时延 {sample.MaxMs} ms");
        if (bits.Count == 1)
            return bits[0] + "。";
        return bits[0] + "，" + string.Join("，", bits.Skip(1)) + "。";
    }

    private static string JitterClause(PingSample sample)
    {
        if (sample.AvgMs is null || sample.MaxMs is null)
            return "";
        return $"到 223.5.5.5 平均时延 {sample.AvgMs} ms，最长时延 {sample.MaxMs} ms。";
    }

    private static List<string> JitterPoints(PingSample gateway, PingSample internet)
    {
        if (Jitter(internet) && internet.AvgMs is not null && internet.MaxMs is not null)
        {
            if (gateway.Received <= 0 && gateway.Sent > 0)
            {
                return
                [
                    $"到路由器的 ping 没有回应。所有测试包均超时，未测得延迟。每个测试包等待 {Config.PingWaitMs} ms。",
                    $"到 223.5.5.5 平均时延 {internet.AvgMs} ms，最长时延 {internet.MaxMs} ms，时延波动明显。",
                ];
            }
            return
            [
                "两段测试均收到回复，但最长时延明显高于平均时延。",
                $"到 223.5.5.5 平均时延 {internet.AvgMs} ms，最长时延 {internet.MaxMs} ms。",
            ];
        }
        if (gateway.AvgMs is not null && gateway.MaxMs is not null)
        {
            return
            [
                "到互联网的时延较为稳定。",
                $"电脑到路由器的时延出现明显升高：平均时延 {gateway.AvgMs} ms，最长时延 {gateway.MaxMs} ms。",
                "更符合无线链路抖动的特征。",
            ];
        }
        return ["时延出现明显升高，属于链路抖动。"];
    }

    public static string Points(IEnumerable<string> items)
    {
        var lines = items.Select(item => item.Trim()).Where(item => item.Length > 0).ToList();
        if (lines.Count == 0)
            return "";
        if (lines.Count == 1)
            return lines[0];
        return string.Join("\n", lines.Select((line, index) => $"{index + 1}. {line}"));
    }

    private static string FirstNonEmpty(params string[] values) =>
        values.FirstOrDefault(value => !string.IsNullOrEmpty(value)) ?? "";

    private static string Or(string value, string fallback) =>
        string.IsNullOrEmpty(value) ? fallback : value;
}
