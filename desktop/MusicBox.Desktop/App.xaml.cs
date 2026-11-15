using System;
using System.Diagnostics;
using Microsoft.UI.Xaml;
using MusicBox.Desktop.Services;

namespace MusicBox.Desktop;

public partial class App : Application
{
    private Window? _window;

    /// <summary>Shared API client for the whole app.</summary>
    public static ApiClient Api { get; private set; } = null!;

    /// <summary>
    /// Shared audio player. Created in OnLaunched, not in a static field
    /// initialiser: MediaPlayer activates WinRT/COM, which is not ready until
    /// the XAML runtime has started.
    /// </summary>
    public static PlayerService Player { get; private set; } = null!;

    public static Window? MainWindowInstance { get; private set; }

    /// <summary>The Python backend process owned by this shell, if we started one.</summary>
    public static Process? Backend { get; set; }
    public static string DataDirectory { get; private set; } = "";

    public App()
    {
        ProbeTrace.Write("App constructor before InitializeComponent");
        InitializeComponent();
        ProbeTrace.Write("App constructor completed");
        if (!string.IsNullOrEmpty(Environment.GetEnvironmentVariable("MUSICBOX_OFFICIAL_LOGIN_PROBE")))
        {
            UnhandledException += (_, e) => ProbeTrace.Write("XAML unhandled " + e.Exception.GetType().Name);
        }
    }

    protected override void OnLaunched(LaunchActivatedEventArgs args)
    {
        var probeDirectory = Environment.GetEnvironmentVariable("MUSICBOX_ACCEPTANCE_DIR");
        if (!string.IsNullOrEmpty(probeDirectory))
        {
            var web = new Microsoft.UI.Xaml.Controls.WebView2();
            var probe = new Window { Title = "MusicBox · 验收探针", Content = web };
            _window = probe;
            var probeStarted = false;
            web.Loaded += async (_, _) =>
            {
                if (probeStarted) return;
                probeStarted = true;
                await Services.AcceptanceProbe.RunAsync(probe, web, probeDirectory);
            };
            probe.Activate();
            return;
        }
        ProbeTrace.Write("OnLaunched resolve data");
        DataDirectory = AppDataPaths.Resolve(AppContext.BaseDirectory,
            Environment.GetEnvironmentVariable("MUSICBOX_HOME"),
            Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData));
        Environment.SetEnvironmentVariable("MUSICBOX_HOME", DataDirectory);
        // Use a private port unless the user explicitly selected an API. A
        // healthy but old backend on 8777 must not mask the new login code.
        var port = ExtractPort(Environment.GetEnvironmentVariable("MUSICBOX_API")) ?? FreePort();
        var state = BackendLauncher.Start(out var backendMessage, port);
        var url = Environment.GetEnvironmentVariable("MUSICBOX_API") ?? $"http://127.0.0.1:{port}";

        ProbeTrace.Write("Backend ready state=" + state);
        Api = new ApiClient(url);
        ProbeTrace.Write("Before media player");
        Player = new PlayerService();
        Player.Attach(Api);

        ProbeTrace.Write("Before MainWindow");
        _window = new MainWindow();
        ProbeTrace.Write("After MainWindow");
        MainWindowInstance = _window;
        _window.Activate();

        if (state == BackendState.Failed)
        {
            _ = (MainWindowInstance as MainWindow)?.ShowBackendErrorAsync(backendMessage);
        }
        else if (!string.IsNullOrEmpty(backendMessage) && state == BackendState.AlreadyRunning)
        {
            (MainWindowInstance as MainWindow)?.SetStatus(backendMessage);
        }

        var officialProbe = Environment.GetEnvironmentVariable("MUSICBOX_OFFICIAL_LOGIN_PROBE");
        if (!string.IsNullOrEmpty(officialProbe))
            _window.DispatcherQueue.TryEnqueue(async () => await ((MainWindow)_window).ProbeOfficialLoginAsync(officialProbe));

        var consoleProbe = Environment.GetEnvironmentVariable("MUSICBOX_CONSOLE_PROBE");
        if (!string.IsNullOrEmpty(consoleProbe))
            _window.DispatcherQueue.TryEnqueue(async () => await ((MainWindow)_window).ProbeConsoleAsync(consoleProbe));

        var transportProbe = Environment.GetEnvironmentVariable("MUSICBOX_TRANSPORT_PROBE");
        if (!string.IsNullOrEmpty(transportProbe))
            _window.DispatcherQueue.TryEnqueue(async () => await ((MainWindow)_window).CaptureTransportAsync(transportProbe));

        _window.Closed += (_, _) =>
        {
            try
            {
                Player.Dispose();
                Api.Dispose();
                if (Backend is { HasExited: false })
                {
                    Backend.Kill(entireProcessTree: true);
                    Backend.WaitForExit(3000);
                }
            }
            catch (Exception) { /* shutdown is best effort */ }
        };
    }

    private static string FreePort()
    {
        var listener = new System.Net.Sockets.TcpListener(System.Net.IPAddress.Loopback, 0);
        listener.Start();
        var port = ((System.Net.IPEndPoint)listener.LocalEndpoint).Port;
        listener.Stop();
        return port.ToString();
    }

    /// <summary>Pull the port out of an explicit MUSICBOX_API override.</summary>
    private static string? ExtractPort(string? url)
    {
        if (string.IsNullOrEmpty(url)) return null;
        try
        {
            var uri = new Uri(url);
            return uri.Port > 0 ? uri.Port.ToString() : null;
        }
        catch (UriFormatException)
        {
            return null;
        }
    }
}
