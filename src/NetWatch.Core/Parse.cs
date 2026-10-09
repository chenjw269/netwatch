using System.Text.RegularExpressions;

namespace NetWatch;

public sealed class AdapterInfo
{
    public AdapterInfo(string name) => Name = name;
    public string Name { get; }
    public bool Disconnected { get; set; }
    public string Ipv4 { get; set; } = "";
    public List<string> Gateways { get; } = [];
}

public static partial class Parse
{
    private static readonly string[] TimeoutMarkers =
    [
        "请求超时", "传输失败", "无法访问目标主机", "目的主机无法访问",
        "timed out", "destination host unreachable", "transmit failed",
    ];

    public static int TimeoutStreak(IReadOnlyList<int?> rtts)
    {
        var best = 0;
        var current = 0;
        foreach (var value in rtts)
        {
            if (value is null)
            {
                current++;
                best = Math.Max(best, current);
            }
            else
            {
                current = 0;
            }
        }
        return best;
    }

    public static PingSample ParsePing(string text, string host)
    {
        var rtts = new List<int?>();
        foreach (var line in text.Split('\n'))
        {
            var folded = line.ToLowerInvariant();
            if (TimeoutMarkers.Any(marker => folded.Contains(marker, StringComparison.OrdinalIgnoreCase) || line.Contains(marker)))
            {
                rtts.Add(null);
                continue;
            }
            if (line.Contains("时间<1") || folded.Contains("time<1"))
            {
                rtts.Add(1);
                continue;
            }
            var match = TimePattern().Match(line);
            if (match.Success)
                rtts.Add(int.Parse(match.Groups[1].Value));
        }

        var sent = FirstInt(SentPattern(), text);
        var received = FirstInt(RecvPattern(), text);
        var loss = FirstInt(LossPattern(), text);
        if (sent is null && rtts.Count > 0)
            sent = rtts.Count;
        if (received is null && rtts.Count > 0)
            received = rtts.Count(item => item is not null);
        if (loss is null && sent is > 0)
            loss = (int)Math.Round(100.0 * (sent.Value - (received ?? 0)) / sent.Value);

        var sample = new PingSample
        {
            Host = host,
            Sent = sent ?? 0,
            Received = received ?? 0,
            LossPct = loss ?? 0,
            MinMs = FirstInt(MinPattern(), text),
            MaxMs = FirstInt(MaxPattern(), text),
            AvgMs = FirstInt(AvgPattern(), text),
            Rtts = rtts,
            MaxTimeoutStreak = TimeoutStreak(rtts),
        };
        if (sample.Sent == 0)
        {
            sample.Error = text.Contains("找不到主机") || text.Contains("could not find host", StringComparison.OrdinalIgnoreCase)
                ? "找不到主机"
                : text.Trim().Length > 0 ? "ping 没有返回统计" : "ping 没有输出";
        }
        else if (rtts.Count == 0 && sample.Received == 0)
        {
            sample.MaxTimeoutStreak = sample.Sent;
        }
        return sample;
    }

    public static (string Gateway, string Interface)? ParseDefaultRoute(string text)
    {
        (int Metric, string Gateway, string Interface)? best = null;
        foreach (var line in text.Split('\n'))
        {
            var parts = line.Split((char[]?)null, StringSplitOptions.RemoveEmptyEntries);
            if (parts.Length < 5 || parts[0] != "0.0.0.0" || parts[1] != "0.0.0.0")
                continue;
            var gateway = parts[2];
            if (gateway.Count(ch => ch == '.') != 3)
                continue;
            if (!int.TryParse(parts[^1], out var metric))
                continue;
            var iface = parts[3];
            if (best is null || metric < best.Value.Metric)
                best = (metric, gateway, iface);
        }
        return best is null ? null : (best.Value.Gateway, best.Value.Interface);
    }

    public static List<AdapterInfo> ParseAdapters(string text)
    {
        var adapters = new List<AdapterInfo>();
        AdapterInfo? current = null;
        var captureGateway = false;
        foreach (var line in text.Split('\n'))
        {
            if (line.Length > 0 && line[0] is not (' ' or '\t'))
            {
                current = new AdapterInfo(line.Trim().TrimEnd(':'));
                adapters.Add(current);
                captureGateway = false;
                continue;
            }
            if (current is null)
                continue;
            var stripped = line.Trim();
            if (line.Contains("媒体状态") || line.Contains("Media State"))
            {
                if (line.Contains("断开") || line.Contains("disconnected", StringComparison.OrdinalIgnoreCase))
                    current.Disconnected = true;
            }
            if (line.Contains("IPv4") || line.Contains("IP Address"))
            {
                var found = Ipv4Pattern().Matches(line);
                if (found.Count > 0 && current.Ipv4.Length == 0)
                    current.Ipv4 = found[0].Groups[1].Value;
            }
            if (line.Contains("默认网关") || line.Contains("Default Gateway"))
            {
                captureGateway = true;
                foreach (Match item in Ipv4Pattern().Matches(line))
                    current.Gateways.Add(item.Groups[1].Value);
                continue;
            }
            if (captureGateway)
            {
                var found = Ipv4Pattern().Matches(stripped);
                if (found.Count > 0 && stripped == found[0].Groups[1].Value)
                {
                    current.Gateways.Add(found[0].Groups[1].Value);
                    continue;
                }
                if (line.Contains(':') || line.Contains('：'))
                    captureGateway = false;
            }
        }
        return adapters;
    }

    public static string ChooseLanGateway(IReadOnlyList<AdapterInfo> adapters, string interfaceIp)
    {
        var privates = adapters.Where(item => PrivateGateways(item).Count > 0).ToList();
        if (interfaceIp.Length > 0)
        {
            foreach (var item in privates)
            {
                if (item.Ipv4 == interfaceIp)
                    return PrivateGateways(item)[0];
            }
        }
        foreach (var item in privates)
        {
            if (!item.Disconnected)
                return PrivateGateways(item)[0];
        }
        return "";
    }

    public static WifiInfo? ParseWifi(string text)
    {
        var blocks = WifiSplit().Split(text);
        WifiInfo? connected = null;
        foreach (var block in blocks)
        {
            var info = ParseWifiBlock(block);
            if (info is { Connected: true })
            {
                connected = info;
                break;
            }
        }
        return connected;
    }

    public static bool IsPrivate(string ip)
    {
        var parts = ip.Split('.');
        if (parts.Length != 4)
            return false;
        if (!int.TryParse(parts[0], out var a) || !int.TryParse(parts[1], out var b))
            return false;
        if (a == 10)
            return true;
        if (a == 192 && b == 168)
            return true;
        return a == 172 && b is >= 16 and <= 31;
    }

    private static WifiInfo? ParseWifiBlock(string text)
    {
        var fields = new Dictionary<string, string>();
        foreach (var line in text.Split('\n'))
        {
            var match = KvPattern().Match(line);
            if (!match.Success)
                continue;
            var canon = CanonWifiKey(match.Groups[1].Value.Trim().ToLowerInvariant());
            if (canon.Length > 0 && !fields.ContainsKey(canon))
                fields[canon] = match.Groups[2].Value.Trim();
        }
        if (fields.Count == 0)
            return null;
        var state = fields.GetValueOrDefault("state", "");
        var folded = state.ToLowerInvariant();
        var connected = !(state.Contains("断开") || folded.Contains("disconnect"))
            && (state.Contains("已连接") || folded == "connected" || folded.StartsWith("connected", StringComparison.Ordinal));
        return new WifiInfo
        {
            Ssid = fields.GetValueOrDefault("ssid", ""),
            Band = fields.GetValueOrDefault("band", ""),
            Channel = fields.GetValueOrDefault("channel", ""),
            Width = fields.GetValueOrDefault("width", ""),
            Radio = fields.GetValueOrDefault("radio", ""),
            Signal = FirstNumber(fields.GetValueOrDefault("signal", "")),
            Rssi = SignedNumber(fields.GetValueOrDefault("rssi", "")),
            RxMbps = FloatNumber(fields.GetValueOrDefault("rx", "")),
            Connected = connected,
        };
    }

    private static string CanonWifiKey(string key)
    {
        if (key == "ssid")
            return "ssid";
        if (key is "状态" or "state")
            return "state";
        if (key.Contains("波段") || key == "band")
            return "band";
        if (key is "通道" or "信道" or "channel")
            return "channel";
        if (key.Contains("宽度") || key.Contains("channel width"))
            return "width";
        if (key.Contains("无线电") || key.Contains("radio type"))
            return "radio";
        if (key.StartsWith("接收速率") || key.Contains("receive rate"))
            return "rx";
        if (key.StartsWith("信号") || key == "signal")
            return "signal";
        if (key == "rssi")
            return "rssi";
        return "";
    }

    private static List<string> PrivateGateways(AdapterInfo adapter) =>
        adapter.Gateways.Where(IsPrivate).ToList();

    private static int? FirstInt(Regex pattern, string text)
    {
        var match = pattern.Match(text);
        return match.Success ? int.Parse(match.Groups[1].Value) : null;
    }

    private static int? FirstNumber(string text)
    {
        var match = NumberPattern().Match(text);
        return match.Success ? int.Parse(match.Groups[1].Value) : null;
    }

    private static int? SignedNumber(string text)
    {
        var match = SignedPattern().Match(text);
        return match.Success ? int.Parse(match.Groups[1].Value) : null;
    }

    private static double? FloatNumber(string text)
    {
        var match = FloatPattern().Match(text);
        return match.Success ? double.Parse(match.Groups[1].Value, System.Globalization.CultureInfo.InvariantCulture) : null;
    }

    [GeneratedRegex(@"(?:时间|time)\s*[=<]\s*(\d+)\s*ms", RegexOptions.IgnoreCase)]
    private static partial Regex TimePattern();

    [GeneratedRegex(@"[（(](\d+)\s*%\s*(?:丢失|loss)[)）]", RegexOptions.IgnoreCase)]
    private static partial Regex LossPattern();

    [GeneratedRegex(@"(?:已发送|Sent)\s*=\s*(\d+)", RegexOptions.IgnoreCase)]
    private static partial Regex SentPattern();

    [GeneratedRegex(@"(?:已接收|Received)\s*=\s*(\d+)", RegexOptions.IgnoreCase)]
    private static partial Regex RecvPattern();

    [GeneratedRegex(@"(?:最短|Minimum)\s*=\s*(\d+)\s*ms", RegexOptions.IgnoreCase)]
    private static partial Regex MinPattern();

    [GeneratedRegex(@"(?:最长|Maximum)\s*=\s*(\d+)\s*ms", RegexOptions.IgnoreCase)]
    private static partial Regex MaxPattern();

    [GeneratedRegex(@"(?:平均|Average)\s*=\s*(\d+)\s*ms", RegexOptions.IgnoreCase)]
    private static partial Regex AvgPattern();

    [GeneratedRegex(@"\b(\d{1,3}(?:\.\d{1,3}){3})\b")]
    private static partial Regex Ipv4Pattern();

    [GeneratedRegex(@"^\s*(.+?)\s*[:：]\s*(.*)$")]
    private static partial Regex KvPattern();

    [GeneratedRegex(@"(?=^\s*(?:名称|Name)\s*[:：])", RegexOptions.Multiline)]
    private static partial Regex WifiSplit();

    [GeneratedRegex(@"(\d+)")]
    private static partial Regex NumberPattern();

    [GeneratedRegex(@"(-?\d+)")]
    private static partial Regex SignedPattern();

    [GeneratedRegex(@"(\d+(?:\.\d+)?)")]
    private static partial Regex FloatPattern();
}
