using System.Runtime.InteropServices;
using Microsoft.UI;
using Microsoft.UI.Composition.SystemBackdrops;
using Microsoft.UI.Windowing;
using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;
using Microsoft.UI.Xaml.Controls.Primitives;
using Microsoft.UI.Xaml.Input;
using Microsoft.UI.Xaml.Media;
using Microsoft.UI.Xaml.Shapes;
using Windows.ApplicationModel.DataTransfer;
using Windows.Graphics;
using Windows.UI;
using WinRT;

namespace NetWatch;

public sealed partial class MainWindow : Window
{
    private readonly Settings _settings = SettingsStore.Load();
    private readonly CancellationTokenSource _stop = new();
    private readonly ManualResetEventSlim _wake = new(false);
    private readonly List<int?> _history = [];
    private Snapshot _snapshot = new();
    private Diagnosis _diagnosis = new("idle", "正在检测", "先看电脑到路由器，再看到国内互联网。");
    private bool _dragged;
    private bool _ignorePointer;
    private int _pressX;
    private int _pressY;
    private int _originX;
    private int _originY;
    private bool _deepRunning;
    private DesktopAcrylicController? _acrylic;
    private SystemBackdropConfiguration? _backdrop;
    private bool _shellReady;

    public MainWindow()
    {
        InitializeComponent();
        SystemBackdrop = null;
        if (AppWindow.Presenter is OverlappedPresenter presenter)
        {
            presenter.SetBorderAndTitleBar(false, false);
            presenter.IsResizable = false;
            presenter.IsMaximizable = false;
presenter.IsMinimizable = false;
        }
        ExtendsContentIntoTitleBar = true;
        AppWindow.Resize(new SizeInt32(_settings.Mini ? 300 : _settings.Expanded ? 520 : 420, 80));
        Closed += (_, _) => _stop.Cancel();
        Shell.PointerPressed += OnPointerPressed;
        Shell.PointerMoved += OnPointerMoved;
        Shell.PointerReleased += OnPointerReleased;
        Shell.RightTapped += OnRightTapped;
        Activated += OnActivatedOnce;
        ApplyChrome();
        SyncChrome();
        if (!_settings.AutostartInitialized)
        {
            try { SettingsStore.SetAutostart(true); }
            catch (Exception ex) { SettingsStore.Log(ex.ToString()); }
            _settings.AutostartInitialized = true;
            Save();
        }
        else if (SettingsStore.AutostartEnabled() && SettingsStore.CurrentAutostartCommand() != SettingsStore.AutostartCommand())
        {
            try { SettingsStore.SetAutostart(true); }
            catch (Exception ex) { SettingsStore.Log(ex.ToString()); }
        }
        Task.Run(Loop);
    }

    private void OnActivatedOnce(object sender, WindowActivatedEventArgs args)
    {
        Activated -= OnActivatedOnce;
        var hwnd = Win32.Hwnd(this);
        Win32.HideFromTaskbar(hwnd);
        Win32.RoundCorners(hwnd);
        TryAcrylic(hwnd);
        Win32.Topmost(hwnd, _settings.Topmost);
        Place(first: true);
        _shellReady = true;
    }

    private void TryAcrylic(IntPtr hwnd)
    {
        if (!DesktopAcrylicController.IsSupported())
        {
            Shell.Background = new SolidColorBrush(Color.FromArgb(255, 0x24, 0x2b, 0x3a));
            return;
        }
        _backdrop = new SystemBackdropConfiguration
        {
            IsInputActive = true,
            Theme = SystemBackdropTheme.Dark,
        };
        _acrylic = new DesktopAcrylicController
        {
            TintColor = Color.FromArgb(255, 0x24, 0x2b, 0x3a),
            TintOpacity = 0.82f,
            LuminosityOpacity = 0.02f,
            FallbackColor = Color.FromArgb(255, 0x24, 0x2b, 0x3a),
        };
        _acrylic.AddSystemBackdropTarget(this.As<Microsoft.UI.Composition.ICompositionSupportsSystemBackdrop>());
        _acrylic.SetSystemBackdropConfiguration(_backdrop);
        Activated += (_, e) =>
        {
            if (_backdrop is null)
                return;
            _backdrop.IsInputActive = e.WindowActivationState != WindowActivationState.Deactivated;
        };
    }

    private void Loop()
    {
        while (!_stop.IsCancellationRequested)
        {
            if (_settings.Paused)
            {
                _wake.Wait(300);
                _wake.Reset();
                continue;
            }
            var started = Environment.TickCount64;
            Snapshot snapshot;
            Diagnosis found;
            try
            {
                snapshot = Collector.Collect();
                found = Diagnose.Run(snapshot);
            }
            catch (Exception ex)
            {
                SettingsStore.Log(ex.ToString());
                snapshot = new Snapshot { Error = "这一轮检测失败，下一轮会再试。", CollectedAt = DateTimeOffset.Now.ToUnixTimeSeconds() };
                found = Diagnose.Run(snapshot);
            }
            DispatcherQueue.TryEnqueue(() => Apply(snapshot, found));
            var interval = Config.NormalizeInterval(_settings.IntervalSec) * 1000L;
            var left = interval - (Environment.TickCount64 - started);
            while (left > 0 && !_stop.IsCancellationRequested && !_settings.Paused)
            {
                if (_wake.Wait(TimeSpan.FromMilliseconds(Math.Min(left, 300))))
                {
                    _wake.Reset();
                    break;
                }
                left = interval - (Environment.TickCount64 - started);
            }
            _wake.Reset();
        }
    }

    private void Apply(Snapshot snapshot, Diagnosis found)
    {
        _snapshot = snapshot;
        _diagnosis = found;
        var paused = _settings.Paused;
        var title = paused ? "已暂停" : found.Title;
        var color = paused ? ColorOf("muted") : ColorOf(found.Level);
        TitleText.Text = title;
        MiniTitle.Text = title;
        TitleText.Foreground = new SolidColorBrush(color);
        MiniTitle.Foreground = new SolidColorBrush(color);
        PaintDot(color);
        var latency = SummaryText.Latency(snapshot);
        LatencyText.Text = latency;
        MiniLatency.Text = latency;
        LatencyText.Foreground = new SolidColorBrush(color);
        MiniLatency.Foreground = new SolidColorBrush(color);
        var chip = new SolidColorBrush(Mix(Color.FromArgb(255, 0x24, 0x2b, 0x3a), color, 0.2));
        LatencyChip.Background = chip;
        MiniChip.Background = chip;
        SubText.Text = SummaryText.Subline(snapshot);
        GatewayTitle.Text = $"电脑 → 路由器    {(string.IsNullOrEmpty(snapshot.LanGateway) ? "—" : snapshot.LanGateway)}";
        GatewayStat.Text = SummaryText.PingText(snapshot.GatewayPing);
        InternetStat.Text = SummaryText.PingText(snapshot.InternetPing);
        RuleText.Text = string.IsNullOrEmpty(found.Rule) ? "" : $"命中规则：{found.Rule}";
        DetailBox.Text = found.Detail;
        FillWifi(snapshot.Wifi, found);
        ProxyText.Text = SummaryText.ProxyLine(snapshot.Proxy);
        FillSites(snapshot);
        PushHistory(snapshot);
        DrawHistory();
        if (_shellReady)
            Place(first: false);
    }

    private void FillWifi(WifiInfo? wifi, Diagnosis found)
    {
        if (wifi is not { Connected: true })
        {
            WifiTitle.Text = "没有已连接的无线";
            WifiSub.Text = "如果用的是网线，链路测试仍然有效。";
            WifiHint.Visibility = Visibility.Collapsed;
            return;
        }
        WifiTitle.Text = string.IsNullOrEmpty(wifi.Ssid) ? "无线已连接" : wifi.Ssid;
        var bits = new List<string>();
        if (!string.IsNullOrEmpty(wifi.Band)) bits.Add(wifi.Band);
        if (!string.IsNullOrEmpty(wifi.Channel)) bits.Add($"信道 {wifi.Channel}");
        if (!string.IsNullOrEmpty(wifi.Width)) bits.Add(wifi.Width);
        if (!string.IsNullOrEmpty(wifi.Radio)) bits.Add(wifi.Radio);
        var signal = new List<string>();
        if (wifi.Signal is not null) signal.Add($"信号 {wifi.Signal}%");
        if (wifi.Rssi is not null) signal.Add($"RSSI {wifi.Rssi}");
        if (wifi.RxMbps is not null) signal.Add($"速率 {wifi.RxMbps.Value:g} Mbps");
        WifiSub.Text = string.Join("\n", new[] { string.Join(" · ", bits), string.Join(" · ", signal) }.Where(part => part.Length > 0));
        var show = wifi.Band.Contains("2.4") && found.Title != "电脑→路由器 2.4GHz 干扰";
        if (show)
        {
            var strong = (wifi.Signal is not null && wifi.Signal >= 70) || (wifi.Rssi is not null && wifi.Rssi >= -65);
            WifiHint.Text = strong
                ? "信号本身不弱。2.4 GHz 容易被隔壁网络干扰，突然变慢或连续超时时，网卡仍会显示已连接。"
                : "当前在 2.4 GHz。信号如果继续变差，延迟会先抖。";
            WifiHint.Visibility = Visibility.Visible;
        }
        else
        {
            WifiHint.Visibility = Visibility.Collapsed;
        }
    }

    private void FillSites(Snapshot snapshot)
    {
        SiteList.Children.Clear();
        if (snapshot.Sites.Count == 0)
        {
            SiteList.Children.Add(new TextBlock
            {
                Text = "等待这一轮连接测试",
                Foreground = new SolidColorBrush(Color.FromArgb(255, 0x5e, 0x68, 0x78)),
                FontFamily = new FontFamily("Microsoft YaHei UI"),
                FontSize = 13,
            });
            return;
        }
        foreach (var site in snapshot.Sites)
        {
            var ok = site.Ok && site.Ms is not null;
            var text = ok ? $"{site.Ms} ms" : (string.IsNullOrEmpty(site.Error) ? "失败" : site.Error);
            var color = !ok ? ColorOf("bad") : site.Ms < 400 ? ColorOf("ok") : ColorOf("warn");
            var row = new StackPanel { Spacing = 2 };
            var line = new Grid();
            line.ColumnDefinitions.Add(new ColumnDefinition { Width = GridLength.Auto });
            line.ColumnDefinitions.Add(new ColumnDefinition { Width = new GridLength(1, GridUnitType.Star) });
            line.ColumnDefinitions.Add(new ColumnDefinition { Width = GridLength.Auto });
            var dot = new Ellipse { Width = 8, Height = 8, Fill = new SolidColorBrush(color), VerticalAlignment = VerticalAlignment.Center, Margin = new Thickness(0, 0, 6, 0) };
            var name = new TextBlock { Text = site.Name, Foreground = new SolidColorBrush(Color.FromArgb(255, 0x1b, 0x23, 0x30)), FontFamily = new FontFamily("Microsoft YaHei UI"), FontSize = 14, VerticalAlignment = VerticalAlignment.Center };
            var value = new TextBlock { Text = text, Foreground = new SolidColorBrush(color), FontFamily = new FontFamily("Microsoft YaHei UI"), FontSize = 14, VerticalAlignment = VerticalAlignment.Center };
            Grid.SetColumn(name, 1);
            Grid.SetColumn(value, 2);
            line.Children.Add(dot);
            line.Children.Add(name);
            line.Children.Add(value);
            row.Children.Add(line);
            var address = SummaryText.SiteAddress(site);
            if (address.Length > 0)
            {
                row.Children.Add(new TextBlock
                {
                    Text = address,
                    Margin = new Thickness(14, 0, 0, 0),
                    Foreground = new SolidColorBrush(Color.FromArgb(255, 0x5e, 0x68, 0x78)),
                    FontFamily = new FontFamily("Microsoft YaHei UI"),
                    FontSize = 13,
                });
            }
            SiteList.Children.Add(row);
        }
    }

    private void PushHistory(Snapshot snapshot)
    {
        var sample = snapshot.InternetPing;
        if (sample.Sent <= 0 && snapshot.CollectedAt <= 0)
            return;
        _history.Add(sample.Replied ? sample.AvgMs : null);
        if (_history.Count > 30)
            _history.RemoveRange(0, _history.Count - 30);
    }

    private void DrawHistory()
    {
        HistoryBars.Children.Clear();
        var data = _history.TakeLast(28).ToList();
        var values = data.Where(item => item is not null).Select(item => item!.Value).ToList();
        var peak = Math.Max(values.Count == 0 ? 50 : values.Max(), 50);
        for (var index = 0; index < 28; index++)
        {
            var slot = index - (28 - data.Count);
            int? value = slot >= 0 && slot < data.Count ? data[slot] : null;
            var shown = slot >= 0;
            double height = 6;
            var color = Color.FromArgb(255, 0xd5, 0xdb, 0xe6);
            if (shown && value is null)
            {
                height = 36;
                color = ColorOf("bad");
            }
            else if (shown && value is int ms)
            {
                height = Math.Max(6, ms / (double)peak * 34);
                color = ms < 80 ? ColorOf("ok") : ms < 150 ? ColorOf("warn") : ColorOf("bad");
            }
            HistoryBars.Children.Add(new Border
            {
                Width = 6,
                Height = shown ? height : 0,
                Background = new SolidColorBrush(shown ? color : Color.FromArgb(0, 0, 0, 0)),
                CornerRadius = new CornerRadius(3),
                VerticalAlignment = VerticalAlignment.Bottom,
                Opacity = shown ? 1 : 0,
            });
        }
    }

    private void SyncChrome()
    {
        MiniBar.Visibility = _settings.Mini ? Visibility.Visible : Visibility.Collapsed;
        Full.Visibility = _settings.Mini ? Visibility.Collapsed : Visibility.Visible;
        var details = _settings.Expanded && !_settings.Mini;
        DetailHost.Visibility = details ? Visibility.Visible : Visibility.Collapsed;
        Actions.Visibility = details ? Visibility.Visible : Visibility.Collapsed;
        Full.Width = details ? 486 : double.NaN;
        ExpandButton.Content = _settings.Expanded ? "收起详情" : "展开详情";
        Shell.Padding = _settings.Mini ? new Thickness(8) : new Thickness(16, 12, 16, 12);
        AutostartButton.Content = SettingsStore.AutostartEnabled() ? "开机启动：开" : "开机启动：关";
        PauseButton.Content = _settings.Paused ? "继续" : "暂停";
        PauseButton.Background = new SolidColorBrush(_settings.Paused ? Color.FromArgb(255, 0x5c, 0x46, 0x30) : Color.FromArgb(255, 0x34, 0x3d, 0x52));
        TopmostButton.Content = _settings.Topmost ? "顶层显示：是" : "顶层显示：否";
        MarkInterval(Interval60, 60);
        MarkInterval(Interval180, 180);
        MarkInterval(Interval300, 300);
        MarkInterval(Interval600, 600);
    }

    private void MarkInterval(Button button, int seconds)
    {
        var on = Config.NormalizeInterval(_settings.IntervalSec) == seconds;
        button.Background = new SolidColorBrush(on ? Color.FromArgb(255, 0x3d, 0x4f, 0x92) : Color.FromArgb(255, 0x34, 0x3d, 0x52));
    }

    private void Place(bool first)
    {
        SyncChrome();
        var expanded = _settings.Expanded && !_settings.Mini;
        var width = expanded ? 520 : ContentWidth();
        var height = ContentHeight(expanded);
        var area = DisplayArea.GetFromWindowId(AppWindow.Id, DisplayAreaFallback.Primary).WorkArea;
        var x = _settings.X ?? Math.Max(area.X + 8, area.X + area.Width - width - 24);
        var y = _settings.Y ?? Math.Max(area.Y + 8, area.Y + area.Height - height - 48);
        var room = area.X + area.Width - x - 8;
        if (room >= 300)
            width = Math.Min(width, room);
        AppWindow.Resize(new SizeInt32(width, height));
        if (first)
            AppWindow.Move(new PointInt32(x, y));
    }

    private int ContentWidth()
    {
        if (_settings.Mini)
        {
            var chip = TextWidth(MiniLatency.Text, 17, true) + 20;
            var title = TextWidth(MiniTitle.Text, 17, true);
            return (int)Math.Ceiling(16 + 96 + 8 + 22 + 8 + title + 8 + chip + 20);
        }
        var row = 22 + 8 + TextWidth(TitleText.Text, 17, true) + 8 + TextWidth(LatencyText.Text, 17, true) + 20;
        var sub = SubText.Text.Split('\n').Select(line => TextWidth(line, 13, false)).DefaultIfEmpty(0).Max();
        return (int)Math.Ceiling(32 + 120 + 16 + Math.Max(row, sub) + 12);
    }

    private int ContentHeight(bool expanded)
    {
        if (_settings.Mini)
            return 56;
        if (!expanded)
            return 128;
        Shell.UpdateLayout();
        Full.Measure(new Windows.Foundation.Size(520, 4000));
        return Math.Max(280, (int)Math.Ceiling(Full.DesiredSize.Height) + 28);
    }

    private static double TextWidth(string text, double size, bool bold)
    {
        var block = new TextBlock
        {
            Text = text,
            FontFamily = new FontFamily("Microsoft YaHei UI"),
            FontSize = size,
            FontWeight = bold ? Microsoft.UI.Text.FontWeights.Bold : Microsoft.UI.Text.FontWeights.Normal,
        };
        block.Measure(new Windows.Foundation.Size(4000, 200));
        return block.DesiredSize.Width;
    }

    private void Save() => SettingsStore.Save(_settings);

    private void ToggleMini(object sender, RoutedEventArgs e)
    {
        _settings.Mini = !_settings.Mini;
        if (_settings.Mini)
            _settings.Expanded = false;
        Save();
        Place(false);
    }

    private void ToggleExpand(object sender, RoutedEventArgs e)
    {
        _settings.Expanded = !_settings.Expanded;
        Save();
        Place(false);
    }

    private void SetInterval60(object sender, RoutedEventArgs e) => SetInterval(60);
    private void SetInterval180(object sender, RoutedEventArgs e) => SetInterval(180);
    private void SetInterval300(object sender, RoutedEventArgs e) => SetInterval(300);
    private void SetInterval600(object sender, RoutedEventArgs e) => SetInterval(600);

    private void SetInterval(int seconds)
    {
        _settings.IntervalSec = Config.NormalizeInterval(seconds);
        Save();
        SyncChrome();
    }

    private void ToggleAutostart(object sender, RoutedEventArgs e)
    {
        try { SettingsStore.SetAutostart(!SettingsStore.AutostartEnabled()); }
        catch (Exception ex) { SettingsStore.Log(ex.ToString()); }
        SyncChrome();
    }

    private void TogglePause(object sender, RoutedEventArgs e)
    {
        _settings.Paused = !_settings.Paused;
        Save();
        TitleText.Text = _settings.Paused ? "已暂停" : _diagnosis.Title;
        MiniTitle.Text = TitleText.Text;
        var color = _settings.Paused ? ColorOf("muted") : ColorOf(_diagnosis.Level);
        TitleText.Foreground = new SolidColorBrush(color);
        MiniTitle.Foreground = new SolidColorBrush(color);
        PaintDot(color);
        SyncChrome();
        if (!_settings.Paused)
            _wake.Set();
    }

    private void ToggleTopmost(object sender, RoutedEventArgs e)
    {
        _settings.Topmost = !_settings.Topmost;
        Save();
        Win32.Topmost(Win32.Hwnd(this), _settings.Topmost);
        SyncChrome();
    }

    private void ExitApp(object sender, RoutedEventArgs e)
    {
        _stop.Cancel();
        _wake.Set();
        Close();
    }

    private void StartDeep(object sender, RoutedEventArgs e)
    {
        if (_deepRunning)
            return;
        _settings.Mini = false;
        _settings.Expanded = true;
        Save();
        Place(false);
        _deepRunning = true;
        DeepButton.IsEnabled = false;
        DeepButton.Content = "正在测试…";
        DeepText.Visibility = Visibility.Visible;
        DeepText.Text = "正在测丢包率，请稍等。";
        Task.Run(() =>
        {
            var sample = Collector.PingHost(Config.InternetHost, Config.DeepPingCount);
            DispatcherQueue.TryEnqueue(() =>
            {
                _deepRunning = false;
                DeepButton.IsEnabled = true;
                DeepButton.Content = "丢包率测试";
                if (sample.Sent == 0)
                    DeepText.Text = string.IsNullOrEmpty(sample.Error) ? "丢包率测试失败" : sample.Error;
                else
                {
                    var avg = sample.AvgMs is null ? "" : $"，平均 {sample.AvgMs} ms";
                    var longest = sample.MaxMs is null ? "" : $"，最长 {sample.MaxMs} ms";
                    DeepText.Text = $"丢包率 {sample.LossPct}%{avg}{longest}";
                }
                Place(false);
            });
        });
    }

    private void CopyDiagnosis(object sender, RoutedEventArgs e)
    {
        var package = new DataPackage();
        package.SetText(SummaryText.Report(_diagnosis, _snapshot, WifiSub.Text));
        Clipboard.SetContent(package);
        CopiedPopup.IsOpen = true;
        Task.Delay(900).ContinueWith(_ => DispatcherQueue.TryEnqueue(() => CopiedPopup.IsOpen = false));
    }

    private void OnRightTapped(object sender, RightTappedRoutedEventArgs e)
    {
        var menu = new MenuFlyout();
        menu.Items.Add(Item("立即检测", () => { if (!_settings.Paused) _wake.Set(); }));
        menu.Items.Add(Item(_settings.Paused ? "继续监测" : "暂停监测", () => TogglePause(this, new RoutedEventArgs())));
        menu.Items.Add(Item($"间隔：{Config.IntervalLabel(Config.NormalizeInterval(_settings.IntervalSec))}", CycleInterval));
        menu.Items.Add(Item(SettingsStore.AutostartEnabled() ? "开机启动：开" : "开机启动：关", () => ToggleAutostart(this, new RoutedEventArgs())));
        menu.Items.Add(Item(_settings.Topmost ? "顶层显示：是" : "顶层显示：否", () => ToggleTopmost(this, new RoutedEventArgs())));
        menu.Items.Add(Item("丢包率测试", () => StartDeep(this, new RoutedEventArgs())));
        menu.Items.Add(Item("复制诊断结果", () => CopyDiagnosis(this, new RoutedEventArgs())));
        menu.Items.Add(new MenuFlyoutSeparator());
        menu.Items.Add(Item("退出", () => ExitApp(this, new RoutedEventArgs())));
        menu.ShowAt(Shell, e.GetPosition(Shell));
        e.Handled = true;
    }

    private void CycleInterval()
    {
        var choices = Config.IntervalChoices;
        var current = Config.NormalizeInterval(_settings.IntervalSec);
        var index = Array.IndexOf(choices, current);
        SetInterval(choices[(index + 1) % choices.Length]);
    }

    private static MenuFlyoutItem Item(string text, Action action)
    {
        var item = new MenuFlyoutItem { Text = text };
        item.Click += (_, _) => action();
        return item;
    }

    private void OnPointerPressed(object sender, PointerRoutedEventArgs e)
    {
        if (IsControl(e.OriginalSource as DependencyObject))
        {
            _ignorePointer = true;
            return;
        }
        _ignorePointer = false;
        var point = Win32.Cursor();
        _pressX = point.X;
        _pressY = point.Y;
        _originX = AppWindow.Position.X;
        _originY = AppWindow.Position.Y;
        _dragged = false;
        Shell.CapturePointer(e.Pointer);
    }

    private void OnPointerMoved(object sender, PointerRoutedEventArgs e)
    {
        if (_ignorePointer || !e.GetCurrentPoint(Shell).Properties.IsLeftButtonPressed)
            return;
        var point = Win32.Cursor();
        var dx = point.X - _pressX;
        var dy = point.Y - _pressY;
        if (Math.Abs(dx) > 3 || Math.Abs(dy) > 3)
            _dragged = true;
        if (_dragged)
            AppWindow.Move(new PointInt32(_originX + dx, _originY + dy));
    }

    private void OnPointerReleased(object sender, PointerRoutedEventArgs e)
    {
        Shell.ReleasePointerCapture(e.Pointer);
        if (_ignorePointer)
            return;
        if (_dragged)
        {
            _settings.X = AppWindow.Position.X;
            _settings.Y = AppWindow.Position.Y;
            Save();
            return;
        }
        if (_settings.Mini)
        {
            _settings.Mini = false;
            Save();
            Place(false);
            return;
        }
        _settings.Expanded = !_settings.Expanded;
        Save();
        Place(false);
    }

    private static bool IsControl(DependencyObject? node)
    {
        while (node != null)
        {
            if (node is Button or TextBox or ScrollBar)
                return true;
            node = VisualTreeHelper.GetParent(node);
        }
        return false;
    }

    private void PaintDot(Color color)
    {
        var brush = new SolidColorBrush(color);
        var halo = new SolidColorBrush(Mix(Color.FromArgb(255, 0x24, 0x2b, 0x3a), color, 0.45));
        foreach (var core in new[] { DotCore, MiniCore })
            core.Fill = brush;
        foreach (var ring in new[] { DotRing, MiniRing })
        {
            ring.Stroke = brush;
            ring.Fill = new SolidColorBrush(Colors.Transparent);
        }
        foreach (var item in new[] { DotHalo, MiniHalo })
            item.Fill = halo;
    }

    private void ApplyChrome()
    {
        // 先把按钮和文字摆上，避免第一帧是空的。
        SyncChrome();
        PaintDot(ColorOf("idle"));
        MiniTitle.Text = "正在检测";
        MiniLatency.Text = "…";
    }

    private static Color ColorOf(string level) => level switch
    {
        "ok" => Color.FromArgb(255, 0x3d, 0xbe, 0x86),
        "warn" => Color.FromArgb(255, 0xe0, 0xa8, 0x4a),
        "bad" => Color.FromArgb(255, 0xef, 0x6a, 0x73),
        "muted" => Color.FromArgb(255, 0x8e, 0x96, 0xa8),
        _ => Color.FromArgb(255, 0x8b, 0x9b, 0xff),
    };

    private static Color Mix(Color baseColor, Color tint, double amount)
    {
        byte Channel(byte from, byte to) => (byte)Math.Clamp(Math.Round(from * (1 - amount) + to * amount), 0, 255);
        return Color.FromArgb(255, Channel(baseColor.R, tint.R), Channel(baseColor.G, tint.G), Channel(baseColor.B, tint.B));
    }
}

internal static class Win32
{
    public static IntPtr Hwnd(Window window) => WinRT.Interop.WindowNative.GetWindowHandle(window);

    public static void HideFromTaskbar(IntPtr hwnd)
    {
        const int style = -20;
        var ex = GetWindowLong(hwnd, style);
        ex |= 0x00000080;
        ex &= ~0x00040000;
        SetWindowLong(hwnd, style, ex);
    }

    public static void RoundCorners(IntPtr hwnd)
    {
        var preference = 2;
        DwmSetWindowAttribute(hwnd, 33, ref preference, 4);
    }

    public static void Topmost(IntPtr hwnd, bool on) =>
        SetWindowPos(hwnd, on ? new IntPtr(-1) : new IntPtr(-2), 0, 0, 0, 0, 0x0001 | 0x0002 | 0x0010);

    public static (int X, int Y) Cursor()
    {
        GetCursorPos(out var point);
        return (point.X, point.Y);
    }

    [DllImport("user32.dll")]
    private static extern int GetWindowLong(IntPtr hwnd, int index);

    [DllImport("user32.dll")]
    private static extern int SetWindowLong(IntPtr hwnd, int index, int value);

    [DllImport("user32.dll")]
    private static extern bool SetWindowPos(IntPtr hwnd, IntPtr insertAfter, int x, int y, int cx, int cy, uint flags);

    [DllImport("dwmapi.dll")]
    private static extern int DwmSetWindowAttribute(IntPtr hwnd, int attribute, ref int value, int size);

    [StructLayout(LayoutKind.Sequential)]
    private struct POINT
    {
        public int X;
        public int Y;
    }

    [DllImport("user32.dll")]
    private static extern bool GetCursorPos(out POINT point);
}
