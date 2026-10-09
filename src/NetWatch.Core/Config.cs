namespace NetWatch;

public static class Config
{
    public const string InternetHost = "223.5.5.5";
    public const int PingCount = 4;
    public const int PingWaitMs = 800;
    public const int DeepPingCount = 20;
    public const int DefaultIntervalSec = 180;
    public const int HttpTimeoutSec = 4;

    public static readonly int[] IntervalChoices = [60, 180, 300, 600];
    public static readonly (string Name, string Url)[] DomesticSites =
    [
        ("百度", "https://www.baidu.com"),
        ("腾讯", "https://www.qq.com"),
    ];
    public static readonly (string Name, string Url)[] ForeignSites =
    [
        ("Google", "https://www.gstatic.com/generate_204"),
        ("Cloudflare", "https://www.cloudflare.com/cdn-cgi/trace"),
    ];
    public static readonly int[] ProxyPorts = [7897, 7890, 7891, 7892, 7893, 10809, 10808, 1080];

    public static int NormalizeInterval(object? value)
    {
        if (value is int number && IntervalChoices.Contains(number))
            return number;
        if (value is long wide && wide is >= int.MinValue and <= int.MaxValue && IntervalChoices.Contains((int)wide))
            return (int)wide;
        return DefaultIntervalSec;
    }

    public static string IntervalLabel(int seconds) => $"{seconds / 60} 分钟";
}
