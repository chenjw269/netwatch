using System.Text.Json;
using System.Text.Json.Serialization;

namespace NetWatch;

public sealed class Settings
{
    [JsonPropertyName("autostart_initialized")]
    public bool AutostartInitialized { get; set; }

    [JsonPropertyName("x")]
    public int? X { get; set; }

    [JsonPropertyName("y")]
    public int? Y { get; set; }

    [JsonPropertyName("expanded")]
    public bool Expanded { get; set; }

    [JsonPropertyName("mini")]
    public bool Mini { get; set; }

    [JsonPropertyName("interval_sec")]
    public int IntervalSec { get; set; } = Config.DefaultIntervalSec;

    [JsonPropertyName("paused")]
    public bool Paused { get; set; }

    [JsonPropertyName("topmost")]
    public bool Topmost { get; set; } = true;
}

public static class SettingsStore
{
    private static readonly JsonSerializerOptions Json = new()
    {
        WriteIndented = true,
        DefaultIgnoreCondition = JsonIgnoreCondition.Never,
    };

    public static string DirectoryPath =>
        Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.ApplicationData), "NetWatch");

    public static string FilePath => Path.Combine(DirectoryPath, "settings.json");

    public static Settings Load()
    {
        try
        {
            if (!File.Exists(FilePath))
                return new Settings();
            var data = JsonSerializer.Deserialize<Settings>(File.ReadAllText(FilePath), Json);
            return data ?? new Settings();
        }
        catch (Exception)
        {
            return new Settings();
        }
    }

    public static void Save(Settings settings)
    {
        Directory.CreateDirectory(DirectoryPath);
        File.WriteAllText(FilePath, JsonSerializer.Serialize(settings, Json));
    }

    public static void Log(string message)
    {
        try
        {
            Directory.CreateDirectory(DirectoryPath);
            var path = Path.Combine(DirectoryPath, "error.log");
            if (File.Exists(path) && new FileInfo(path).Length > 200_000)
                File.WriteAllText(path, "");
            File.AppendAllText(path, message.TrimEnd() + Environment.NewLine);
        }
        catch (Exception)
        {
            // 日志写失败时不再抛。
        }
    }

    public static bool AutostartEnabled()
    {
        try
        {
            using var key = Microsoft.Win32.Registry.CurrentUser.OpenSubKey(
                @"Software\Microsoft\Windows\CurrentVersion\Run");
            return key?.GetValue("NetWatch") is string text && text.Length > 0;
        }
        catch (Exception)
        {
            return false;
        }
    }

    public static string CurrentAutostartCommand()
    {
        try
        {
            using var key = Microsoft.Win32.Registry.CurrentUser.OpenSubKey(
                @"Software\Microsoft\Windows\CurrentVersion\Run");
            return key?.GetValue("NetWatch") as string ?? "";
        }
        catch (Exception)
        {
            return "";
        }
    }

    public static string AutostartCommand()
    {
        var path = Environment.ProcessPath ?? "";
        return $"\"{path}\"";
    }

    public static void SetAutostart(bool enabled)
    {
        using var key = Microsoft.Win32.Registry.CurrentUser.OpenSubKey(
            @"Software\Microsoft\Windows\CurrentVersion\Run", writable: true)
            ?? throw new InvalidOperationException("无法打开启动项");
        if (enabled)
            key.SetValue("NetWatch", AutostartCommand());
        else
        {
            try { key.DeleteValue("NetWatch", throwOnMissingValue: false); }
            catch (Exception) { /* 本来就没有 */ }
        }
    }
}
