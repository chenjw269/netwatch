using System.Diagnostics;
using System.Net;
using System.Net.Sockets;
using System.Text;

namespace NetWatch;

public static class Collector
{
    static Collector()
    {
        Encoding.RegisterProvider(CodePagesEncodingProvider.Instance);
    }

    public static Snapshot Collect()
    {
        string lan;
        string defaultGw;
        bool tun;
        try
        {
            (lan, defaultGw, tun) = DiscoverGateways();
        }
        catch (Exception exc)
        {
            return new Snapshot { Error = $"读取网关失败：{exc.Message}", CollectedAt = Now() };
        }

        var wifiTask = Task.Run(SafeWifi);
        var proxyTask = Task.Run(() => ReadProxy(tun));
        var gatewayTask = Task.Run(() => PingHost(lan, Config.PingCount));
        var internetTask = Task.Run(() => PingHost(Config.InternetHost, Config.PingCount));
        var proxy = proxyTask.GetAwaiter().GetResult();
        var siteTasks = Config.DomesticSites
            .Select(site => Task.Run(() => ProbeSite(site.Name, site.Url, false, null)))
            .ToList();
        if (proxy.ProbeForeign)
        {
            foreach (var site in Config.ForeignSites)
                siteTasks.Add(Task.Run(() => ProbeSite(site.Name, site.Url, true, proxy.HttpProxy)));
        }
        Task.WaitAll([.. siteTasks, wifiTask, gatewayTask, internetTask]);
        return new Snapshot
        {
            LanGateway = lan,
            DefaultGateway = defaultGw,
            TunLikely = tun,
            GatewayPing = gatewayTask.Result,
            InternetPing = internetTask.Result,
            Wifi = wifiTask.Result,
            Sites = siteTasks.Select(task => task.Result).ToList(),
            Proxy = proxy,
            CollectedAt = Now(),
        };
    }

    public static (string Lan, string DefaultGateway, bool Tun) DiscoverGateways()
    {
        var adapters = Parse.ParseAdapters(RunCmd(["ipconfig"], 8));
        var route = Parse.ParseDefaultRoute(RunCmd(["route", "print", "-4"], 8));
        var defaultGw = route?.Gateway ?? "";
        var interfaceIp = route?.Interface ?? "";
        var lan = Parse.ChooseLanGateway(adapters, interfaceIp);
        if (lan.Length == 0 && defaultGw.Length > 0 && Parse.IsPrivate(defaultGw))
            lan = defaultGw;
        var tun = lan.Length > 0 && defaultGw.Length > 0 && defaultGw != lan;
        return (lan, defaultGw, tun);
    }

    public static PingSample PingHost(string host, int count = Config.PingCount)
    {
        if (string.IsNullOrEmpty(host))
            return new PingSample { Host = host, Error = "没有地址" };
        try
        {
            var text = RunCmd(["ping", "-n", count.ToString(), "-w", Config.PingWaitMs.ToString(), host], count + 12);
            return Parse.ParsePing(text, host);
        }
        catch (Exception exc)
        {
            return new PingSample { Host = host, Error = exc.Message };
        }
    }

    public static ProxyState ReadProxy(bool tunLikely)
    {
        var (enabled, server) = RegistryProxy();
        var processes = ClashProcesses();
        int? listenPort = null;
        if (processes.Count > 0 && !(enabled && server.Length > 0))
            listenPort = FirstOpenPort();
        return new ProxyState
        {
            SystemEnabled = enabled,
            Server = server,
            Processes = processes,
            ListenPort = listenPort,
            TunLikely = tunLikely,
        };
    }

    public static SiteProbe ProbeSite(string name, string url, bool foreign, string? proxy)
    {
        var host = SiteHost(url);
        var ip = LookupIpv4(host);
        var started = Stopwatch.GetTimestamp();
        try
        {
            using var handler = new HttpClientHandler { UseProxy = false };
            if (!string.IsNullOrEmpty(proxy))
            {
                handler.UseProxy = true;
                handler.Proxy = new WebProxy(proxy);
            }
            using var client = new HttpClient(handler) { Timeout = TimeSpan.FromSeconds(Config.HttpTimeoutSec) };
            using var request = new HttpRequestMessage(HttpMethod.Get, url);
            request.Headers.UserAgent.ParseAdd("Mozilla/5.0");
            using var response = client.Send(request);
            var buffer = new byte[128];
            response.Content.ReadAsStream().Read(buffer, 0, buffer.Length);
            return new SiteProbe
            {
                Name = name,
                Ok = true,
                Ms = Elapsed(started),
                Foreign = foreign,
                Host = host,
                Ip = ip,
            };
        }
        catch (HttpRequestException exc) when (foreign && exc.StatusCode is >= System.Net.HttpStatusCode.InternalServerError)
        {
            return new SiteProbe
            {
                Name = name,
                Ok = false,
                Ms = Elapsed(started),
                Error = $"HTTP {(int?)exc.StatusCode}",
                Foreign = foreign,
                Host = host,
                Ip = ip,
            };
        }
        catch (Exception exc)
        {
            return new SiteProbe
            {
                Name = name,
                Ok = false,
                Ms = Elapsed(started),
                Error = ShortError(exc),
                Foreign = foreign,
                Host = host,
                Ip = ip,
            };
        }
    }

    public static string SiteHost(string url)
    {
        if (!Uri.TryCreate(url, UriKind.Absolute, out var uri))
            return "";
        return uri.Host;
    }

    public static string LookupIpv4(string host)
    {
        if (string.IsNullOrEmpty(host))
            return "";
        if (IPAddress.TryParse(host, out var parsed) && parsed.AddressFamily == AddressFamily.InterNetwork)
            return host;
        try
        {
            var records = Dns.GetHostAddresses(host);
            var ipv4 = records.FirstOrDefault(item => item.AddressFamily == AddressFamily.InterNetwork);
            return ipv4?.ToString() ?? "";
        }
        catch (Exception)
        {
            return "";
        }
    }

    private static WifiInfo? SafeWifi()
    {
        try
        {
            return Parse.ParseWifi(RunCmd(["netsh", "wlan", "show", "interfaces"], 8));
        }
        catch (Exception)
        {
            return null;
        }
    }

    private static (bool Enabled, string Server) RegistryProxy()
    {
        try
        {
            using var key = Microsoft.Win32.Registry.CurrentUser.OpenSubKey(
                @"Software\Microsoft\Windows\CurrentVersion\Internet Settings");
            if (key is null)
                return (false, "");
            var enabled = Convert.ToInt32(key.GetValue("ProxyEnable") ?? 0) == 1;
            var server = Convert.ToString(key.GetValue("ProxyServer") ?? "")?.Trim() ?? "";
            return (enabled, server);
        }
        catch (Exception)
        {
            return (false, "");
        }
    }

    private static List<string> ClashProcesses()
    {
        string text;
        try
        {
            text = RunCmd(["tasklist", "/FO", "CSV", "/NH"], 12);
        }
        catch (Exception)
        {
            return [];
        }
        var found = new List<string>();
        foreach (var line in text.Split('\n'))
        {
            var trimmed = line.Trim();
            if (!trimmed.StartsWith('"'))
                continue;
            var end = trimmed.IndexOf('"', 1);
            if (end <= 1)
                continue;
            var name = trimmed[1..end];
            var folded = name.ToLowerInvariant();
            if ((folded.Contains("clash") || folded.Contains("mihomo")) && !found.Contains(name))
                found.Add(name);
        }
        return found;
    }

    private static int? FirstOpenPort()
    {
        foreach (var port in Config.ProxyPorts)
        {
            try
            {
                using var client = new TcpClient();
                var task = client.ConnectAsync("127.0.0.1", port);
                if (!task.Wait(120) || !client.Connected)
                    continue;
                return port;
            }
            catch (Exception)
            {
                // 这个端口没有代理在听。
            }
        }
        return null;
    }

    private static string ShortError(Exception exc)
    {
        var text = exc.ToString();
        var folded = text.ToLowerInvariant();
        if (folded.Contains("timed out") || text.Contains("超时") || exc is TaskCanceledException)
            return "超时";
        if (folded.Contains("getaddrinfo") || text.Contains("11001") || text.Contains("名称或服务") || exc is HttpRequestException { InnerException: SocketException { ErrorCode: 11001 } })
            return "DNS 解析失败";
        if (text.Contains("10061") || text.Contains("拒绝"))
            return "连接被拒绝";
        return "连接失败";
    }

    public static string RunCmd(string[] args, int timeoutSec)
    {
        var start = new ProcessStartInfo(args[0], string.Join(" ", args.Skip(1).Select(Quote)))
        {
            RedirectStandardOutput = true,
            RedirectStandardError = true,
            UseShellExecute = false,
            CreateNoWindow = true,
            StandardOutputEncoding = Encoding.GetEncoding(936),
            StandardErrorEncoding = Encoding.GetEncoding(936),
        };
        using var process = Process.Start(start) ?? throw new InvalidOperationException("无法启动 " + args[0]);
        if (!process.WaitForExit(timeoutSec * 1000))
        {
            try { process.Kill(true); } catch { /* 已经退出 */ }
            throw new TimeoutException(args[0]);
        }
        var stdout = process.StandardOutput.ReadToEnd();
        var stderr = process.StandardError.ReadToEnd();
        var data = string.IsNullOrEmpty(stdout) ? stderr : stdout;
        return Decode(data);
    }

    private static string Quote(string value) => value.Contains(' ') ? $"\"{value}\"" : value;

    private static string Decode(string text)
    {
        if (string.IsNullOrEmpty(text))
            return "";
        if (new[] { "已发送", "状态", "SSID", "Sent", "Packets", "0.0.0.0" }.Any(text.Contains))
            return text;
        return text;
    }

    private static int Elapsed(long started) =>
        (int)(Stopwatch.GetElapsedTime(started).TotalMilliseconds);

    private static double Now() => DateTimeOffset.Now.ToUnixTimeSeconds();
}
