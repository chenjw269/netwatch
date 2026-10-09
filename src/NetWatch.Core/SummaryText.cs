namespace NetWatch;

public static class SummaryText
{
    public static string Latency(Snapshot snapshot)
    {
        var sample = snapshot.InternetPing;
        if (sample.Sent <= 0)
            return "…";
        if (!sample.Replied || sample.AvgMs is null)
            return "无回复";
        return $"{sample.AvgMs} ms";
    }

    public static string Subline(Snapshot snapshot)
    {
        if (snapshot.CollectedAt <= 0)
            return "先看电脑到路由器，再看到 223.5.5.5";
        var clock = DateTimeOffset.FromUnixTimeSeconds((long)snapshot.CollectedAt).ToLocalTime().ToString("HH:mm:ss");
        return $"{ShortHop("电脑→路由器", snapshot.GatewayPing)}\n{ShortHop("路由器→互联网", snapshot.InternetPing)}\n上次检测 {clock}";
    }

    public static string ShortHop(string label, PingSample sample)
    {
        if (sample.Sent <= 0)
            return $"{label} —";
        if (!sample.Replied || sample.AvgMs is null)
            return $"{label} 无回复，已等 {Config.PingWaitMs}ms";
        var loss = sample.LossPct > 0 ? $" 丢{sample.LossPct}%" : "";
        return $"{label} {sample.AvgMs}ms{loss}";
    }

    public static string PingText(PingSample sample)
    {
        if (sample.Sent <= 0)
            return string.IsNullOrEmpty(sample.Error) ? "没有数据" : sample.Error;
        if (!sample.Replied)
            return $"无回复 · 已等 {Config.PingWaitMs} ms · 没有测到延迟";
        var avg = sample.AvgMs is null ? "—" : $"{sample.AvgMs} ms";
        var longest = sample.MaxMs is null ? "—" : $"{sample.MaxMs} ms";
        return $"平均 {avg}    最长 {longest}    丢包 {sample.LossPct}%";
    }

    public static string ProxyLine(ProxyState proxy)
    {
        if (proxy.TunLikely)
            return "VPN 接管了默认路由，国内和国外都测";
        if (proxy.SystemProxyOn)
            return $"当前系统代理 {proxy.Server}\n国内网站直接连接\n国外网站通过代理连接";
        if (proxy.ClashRunning)
            return "Clash 在运行，系统代理没开，只测国内";
        return "未开代理，只测国内网站";
    }

    public static string SiteAddress(SiteProbe site)
    {
        if (!string.IsNullOrEmpty(site.Ip))
            return $"{site.Host}   {site.Ip}".Trim();
        return site.Host;
    }

    public static string Report(Diagnosis diagnosis, Snapshot snapshot, string wifiSub)
    {
        var lines = new List<string>
        {
            diagnosis.Title,
            diagnosis.Detail,
            PingText(snapshot.GatewayPing),
            PingText(snapshot.InternetPing),
        };
        if (snapshot.Wifi is { Connected: true } && wifiSub.Length > 0)
            lines.Add(wifiSub);
        foreach (var site in snapshot.Sites)
        {
            var address = SiteAddress(site);
            var prefix = $"{site.Name} {address}".Trim();
            lines.Add(site.Ok && site.Ms is not null ? $"{prefix} {site.Ms} ms" : $"{prefix} {(string.IsNullOrEmpty(site.Error) ? "失败" : site.Error)}");
        }
        return string.Join("\n", lines.Where(line => !string.IsNullOrEmpty(line)));
    }
}
