namespace NetWatch;

public sealed class PingSample
{
    public string Host { get; set; } = "";
    public int Sent { get; set; }
    public int Received { get; set; }
    public int LossPct { get; set; }
    public int? MinMs { get; set; }
    public int? MaxMs { get; set; }
    public int? AvgMs { get; set; }
    public List<int?> Rtts { get; set; } = [];
    public int MaxTimeoutStreak { get; set; }
    public string Error { get; set; } = "";
    public bool Replied => Received > 0 && string.IsNullOrEmpty(Error);
}

public sealed class WifiInfo
{
    public string Ssid { get; set; } = "";
    public string Band { get; set; } = "";
    public string Channel { get; set; } = "";
    public string Width { get; set; } = "";
    public string Radio { get; set; } = "";
    public int? Signal { get; set; }
    public int? Rssi { get; set; }
    public double? RxMbps { get; set; }
    public bool Connected { get; set; }
}

public sealed class SiteProbe
{
    public string Name { get; set; } = "";
    public bool Ok { get; set; }
    public int? Ms { get; set; }
    public string Error { get; set; } = "";
    public bool Foreign { get; set; }
    public string Host { get; set; } = "";
    public string Ip { get; set; } = "";
}

public sealed class ProxyState
{
    public bool SystemEnabled { get; set; }
    public string Server { get; set; } = "";
    public List<string> Processes { get; set; } = [];
    public int? ListenPort { get; set; }
    public bool TunLikely { get; set; }
    public bool SystemProxyOn => SystemEnabled && !string.IsNullOrWhiteSpace(Server);
    public bool ClashRunning => Processes.Count > 0;
    public bool ProbeForeign => SystemProxyOn || TunLikely;
    public string? HttpProxy => SystemProxyOn ? ProxyText.NormalizeProxy(Server) : null;
}

public sealed class Snapshot
{
    public string LanGateway { get; set; } = "";
    public string DefaultGateway { get; set; } = "";
    public bool TunLikely { get; set; }
    public PingSample GatewayPing { get; set; } = new();
    public PingSample InternetPing { get; set; } = new() { Host = Config.InternetHost };
    public WifiInfo? Wifi { get; set; }
    public List<SiteProbe> Sites { get; set; } = [];
    public ProxyState Proxy { get; set; } = new();
    public string Error { get; set; } = "";
    public double CollectedAt { get; set; }
}

public sealed class Diagnosis
{
    public string Level { get; set; } = "idle";
    public string Title { get; set; } = "";
    public string Detail { get; set; } = "";
    public string Rule { get; set; } = "";

    public Diagnosis()
    {
    }

    public Diagnosis(string level, string title, string detail, string rule = "")
    {
        Level = level;
        Title = title;
        Detail = detail;
        Rule = rule;
    }
}

public static class ProxyText
{
    public static string NormalizeProxy(string server)
    {
        var text = server.Trim();
        if (text.Contains('='))
        {
            var parts = new Dictionary<string, string>(StringComparer.OrdinalIgnoreCase);
            foreach (var item in text.Split(';'))
            {
                var cut = item.IndexOf('=');
                if (cut < 0)
                    continue;
                parts[item[..cut].Trim()] = item[(cut + 1)..].Trim();
            }
            if (parts.TryGetValue("https", out var https) && https.Length > 0)
                text = https;
            else if (parts.TryGetValue("http", out var http) && http.Length > 0)
                text = http;
        }
        if (!text.StartsWith("http://", StringComparison.OrdinalIgnoreCase)
            && !text.StartsWith("https://", StringComparison.OrdinalIgnoreCase)
            && !text.StartsWith("socks5://", StringComparison.OrdinalIgnoreCase)
            && !text.StartsWith("socks://", StringComparison.OrdinalIgnoreCase))
            text = "http://" + text;
        return text;
    }
}
