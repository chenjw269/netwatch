using Microsoft.UI.Xaml;

namespace NetWatch;

public partial class NetWatchApp : Application
{
    private Mutex? _mutex;
    private Window? _window;

    public NetWatchApp()
    {
        InitializeComponent();
        UnhandledException += (_, args) =>
        {
            SettingsStore.Log(args.Exception.ToString());
            args.Handled = true;
        };
    }

    protected override void OnLaunched(LaunchActivatedEventArgs args)
    {
        _mutex = new Mutex(false, @"Local\NetWatchFloatingWidget", out var created);
        if (!created)
        {
            Exit();
            return;
        }
        try
        {
            using var process = System.Diagnostics.Process.GetCurrentProcess();
            process.PriorityClass = System.Diagnostics.ProcessPriorityClass.BelowNormal;
        }
        catch (Exception)
        {
            // 优先级降不下来也不影响检测。
        }
        _window = new MainWindow();
        _window.Activate();
    }
}
