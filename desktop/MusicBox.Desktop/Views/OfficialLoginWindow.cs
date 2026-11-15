using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Text.Json;
using System.Threading;
using System.Threading.Tasks;
using Microsoft.UI.Windowing;
using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;
using Microsoft.Web.WebView2.Core;
using MusicBox.Desktop.Services;

namespace MusicBox.Desktop.Views;

/// <summary>Resizable native host for the unmodified official desktop website.
/// No ContentDialog width limit, fixed WebView height or outer ScrollViewer.</summary>
public sealed class OfficialLoginWindow : Window
{
    private readonly WebView2 _web = new();
    private readonly TextBlock _hint = new() { TextWrapping = TextWrapping.Wrap };
    private readonly Button _bind = new() { Content = "完成并绑定", IsEnabled = false };
    private readonly string _provider;
    private readonly string _root;
    private readonly string _url;
    private readonly List<(Window Window, WebView2 Web)> _popups = new();
    private readonly CancellationTokenSource _cts = new();
    private readonly TaskCompletionSource<bool> _completion = new();
    private bool _started;
    private bool _closed;
    private bool _probeCaptured;
    public Task<bool> Completion => _completion.Task;
    public string ProfilePath { get; }
    public Dictionary<string, object?>? Probe { get; set; }

    public OfficialLoginWindow(string provider)
    {
        ProbeTrace.Write("LoginWindow constructor " + provider);
        _provider = provider;
        _root = provider switch { "qq" => "qq.com", "kugou" => "kugou.com", "kuwo" => "kuwo.cn", _ => throw new ApiException("未知平台") };
        _url = provider == "qq" ? "https://y.qq.com/" : "https://www." + _root + "/";
        ProfilePath = AppDataPaths.LoginProfile(App.DataDirectory, provider);
        Title = provider switch { "qq" => "登录 QQ音乐 · MusicBox", "kugou" => "登录酷狗音乐 · MusicBox", _ => "登录酷我音乐 · MusicBox" };
        var grid = new Grid { Background = new Microsoft.UI.Xaml.Media.SolidColorBrush(Microsoft.UI.Colors.White) };
        grid.RowDefinitions.Add(new RowDefinition { Height = GridLength.Auto });
        grid.RowDefinitions.Add(new RowDefinition { Height = new GridLength(1, GridUnitType.Star) });
        grid.RowDefinitions.Add(new RowDefinition { Height = GridLength.Auto });
        _hint.Text = "在官方网页右上角选择登录和扫码方式。手机确认后点击完成并绑定。可最大化窗口；Ctrl + 鼠标滚轮或 Ctrl +/- 调整网页缩放。";
        _hint.Margin = new Thickness(16, 10, 16, 10);
        grid.Children.Add(_hint);
        Grid.SetRow(_web, 1); grid.Children.Add(_web);
        var bar = new StackPanel { Orientation = Orientation.Horizontal, Spacing = 10, Margin = new Thickness(16, 10, 16, 14) };
        var home = new Button { Content = "返回官网首页" };
        home.Click += (_, _) => { if (!_closed && _web.CoreWebView2 is not null) _web.Source = new Uri(_url); };
        var refresh = new Button { Content = "刷新网页" };
        refresh.Click += (_, _) => _web.CoreWebView2?.Reload();
        var cancel = new Button { Content = "取消" }; cancel.Click += (_, _) => Close();
        bar.Children.Add(home); bar.Children.Add(refresh); bar.Children.Add(_bind); bar.Children.Add(cancel);
        Grid.SetRow(bar, 2); grid.Children.Add(bar);
        Content = grid;
        AppWindow.Resize(new Windows.Graphics.SizeInt32(1500, 1000));
        if (AppWindow.Presenter is OverlappedPresenter presenter) presenter.Maximize();
        _web.Loaded += async (_, _) => { if (_started) return; _started = true; await InitializeAsync(); };
        _bind.Click += async (_, _) => await BindAsync();
        Closed += (_, _) =>
        {
            if (_closed) return;
            _closed = true; _cts.Cancel();
            foreach (var popup in _popups.ToArray()) popup.Window.Close();
            _popups.Clear(); _web.Close();
            if (Probe is not null) Probe["closed"] = true;
            _completion.TrySetResult(false);
        };
    }

    private async Task InitializeAsync()
    {
        try
        {
            ProbeTrace.Write("LoginWindow init " + _provider);
            var env = await CoreWebView2Environment.CreateWithOptionsAsync(null, ProfilePath, new CoreWebView2EnvironmentOptions());
            if (_closed) return;
            await _web.EnsureCoreWebView2Async(env);
            if (_closed) return;
            var core = _web.CoreWebView2;
            core.Settings.IsPasswordAutosaveEnabled = false;
            core.Settings.IsGeneralAutofillEnabled = false;
            core.Settings.IsZoomControlEnabled = true;
            core.Settings.AreDevToolsEnabled = false;
            core.NavigationStarting += (_, e) => HandleNavigation(_web, e);
            core.NewWindowRequested += async (_, e) =>
            {
                e.Handled = true;
                if (!Https(e.Uri)) return;
                var deferral = e.GetDeferral();
                Window? popup = null;
                WebView2? child = null;
                try
                {
                    child = new WebView2(); popup = new Window { Title = "官方账号验证", Content = child };
                    popup.AppWindow.Resize(new Windows.Graphics.SizeInt32(1080, 820));
                    var ready = new TaskCompletionSource<bool>(); child.Loaded += (_, _) => ready.TrySetResult(true);
                    popup.Activate(); await ready.Task.WaitAsync(TimeSpan.FromSeconds(10));
                    await child.EnsureCoreWebView2Async(env);
                    if (_closed) { child.Close(); popup.Close(); return; }
                    var owned = child; var owner = popup;
                    child.CoreWebView2.Settings.IsPasswordAutosaveEnabled = false;
                    child.CoreWebView2.NavigationStarting += (_, n) => HandleNavigation(owned, n);
                    child.CoreWebView2.NavigationCompleted += (_, n) =>
                    {
                        if (_closed) return;
                        if (!n.IsSuccess) _hint.Text = "官方授权跳转失败：" + n.WebErrorStatus + "。请重新打开扫码，勿把扫描成功当作已绑定。";
                        else if (Uri.TryCreate(owned.CoreWebView2.Source, UriKind.Absolute, out var at)
                            && at.Host == "www.kuwo.cn" && at.AbsolutePath == "/callback")
                        {
                            _hint.Text = "官方授权已返回酷我。请检查主页登录状态，再点击完成并绑定。";
                            // Give the site's own callback JavaScript time to notify its opener.
                            DispatcherQueue.TryEnqueue(async () =>
                            {
                                await Task.Delay(700);
                                if (!_closed)
                                {
                                    _web.CoreWebView2.Reload();
                                    Activate();
                                }
                            });
                        }
                    };
                    child.CoreWebView2.WindowCloseRequested += (_, _) => owner.Close();
                    popup.Closed += (_, _) => { owned.Close(); _popups.RemoveAll(p => p.Web == owned); };
                    _popups.Add((popup, child)); e.NewWindow = child.CoreWebView2;
                }
                catch (Exception) { child?.Close(); popup?.Close(); if (!_closed) _hint.Text = "官方授权窗口打开失败，请刷新后重试。"; }
                finally { deferral.Complete(); }
            };
            core.NavigationCompleted += async (_, e) =>
            {
                if (_closed) return;
                if (!e.IsSuccess) _hint.Text = "官网加载失败：" + e.WebErrorStatus + "。可刷新或稍后重试。";
                if (Probe is not null)
                {
                    Probe["navigationSucceeded"] = e.IsSuccess;
                    await CaptureProbeAsync();
                }
            };
            if (Probe is not null) { Probe["initialized"] = true; Probe["profile"] = ProfilePath; }
            _web.Source = new Uri(_url); _bind.IsEnabled = true;
        }
        catch (Exception ex)
        {
            if (Probe is not null) { Probe["error"] = ex.GetType().Name + ": " + ex.Message; Close(); }
            else if (!_closed) _hint.Text = "打开登录页失败：" + ex.Message;
        }
    }

    private async Task CaptureProbeAsync()
    {
        if (_probeCaptured) return;
        _probeCaptured = true;
        try
        {
            await Task.Delay(2000, _cts.Token);
            if (_closed || Probe is null) return;
            Probe["webWidth"] = _web.ActualWidth; Probe["webHeight"] = _web.ActualHeight;
            Probe["resizableStandalone"] = true;
            // Read-only layout observation: no DOM/style modifications or login automation.
            Probe["documentMetrics"] = await _web.CoreWebView2.ExecuteScriptAsync(
                "JSON.stringify({viewport:innerWidth,document:document.documentElement.scrollWidth,title:document.title,loginEntries:Array.from(document.querySelectorAll('a,button,span,div')).filter(e=>e.textContent.trim()==='登录').slice(0,8).map(e=>{let r=e.getBoundingClientRect();return {tag:e.tagName,id:e.id,className:e.className,x:r.x,y:r.y,width:r.width,height:r.height,visible:r.width>0&&r.height>0&&r.x>=0&&r.right<=innerWidth&&r.y>=0&&r.bottom<=innerHeight}})})");
            // Acceptance only: open the visible official login control. No
            // credentials are entered, forms submitted or page styles changed.
            Probe["loginEntryOpened"] = await _web.CoreWebView2.ExecuteScriptAsync(
                "(()=>{const e=Array.from(document.querySelectorAll('a,button,span,div')).find(e=>{let r=e.getBoundingClientRect();return e.textContent.trim()==='登录'&&r.width>0&&r.width<180&&r.height>0&&r.height<120&&r.x>=0&&r.right<=innerWidth&&r.y>=0&&r.bottom<=innerHeight});if(e){e.click();return true}return false})()");
            await Task.Delay(2200, _cts.Token);
            var output = Environment.GetEnvironmentVariable("MUSICBOX_OFFICIAL_LOGIN_PROBE");
            if (!string.IsNullOrEmpty(output))
            {
                Directory.CreateDirectory(output);
                using var file = File.Create(Path.Combine(output, _provider + "-login-page.png"));
                using var stream = file.AsRandomAccessStream();
                await _web.CoreWebView2.CapturePreviewAsync(CoreWebView2CapturePreviewImageFormat.Png, stream);
            }
        }
        catch (Exception ex) { if (Probe is not null) Probe["captureError"] = ex.GetType().Name; }
        finally { if (!_closed) Close(); }
    }

    private async Task BindAsync()
    {
        _bind.IsEnabled = false;
        try
        {
            var cookies = await _web.CoreWebView2.CookieManager.GetCookiesAsync(null);
            var owned = cookies.Where(c => { var d = c.Domain.TrimStart('.'); return d.Equals(_root, StringComparison.OrdinalIgnoreCase) || d.EndsWith("." + _root, StringComparison.OrdinalIgnoreCase); })
                .Select(c => new { name = c.Name, value = c.Value, domain = c.Domain, path = c.Path, secure = c.IsSecure }).ToArray();
            if (owned.Length == 0) throw new ApiException("请先在官方网页完成扫码登录");
            await App.Api.SaveCookieAsync(_provider, JsonSerializer.Serialize(new { version = 1, cookies = owned }), _cts.Token);
            _completion.TrySetResult(true); Close();
        }
        catch (OperationCanceledException) { }
        catch (Exception ex) { if (!_closed) _hint.Text = ex.Message; }
        finally { if (!_closed) _bind.IsEnabled = true; }
    }

    private void HandleNavigation(WebView2 view, CoreWebView2NavigationStartingEventArgs args)
    {
        if (Https(args.Uri)) return;
        args.Cancel = true;
        var secure = OfficialNavigationPolicy.SecureCallback(_provider, args.Uri);
        if (secure is not null)
        {
            _hint.Text = "正在安全完成酷我官方授权回调…";
            // Cancel before network I/O, then repeat only the approved official
            // callback at TLS. Never log the URI: it carries the OAuth code.
            DispatcherQueue.TryEnqueue(() => { if (!_closed) view.CoreWebView2.Navigate(secure.AbsoluteUri); });
        }
        else if (!_closed) _hint.Text = "已阻止非安全登录跳转。请重新打开官网；若仍停在扫描成功，请反馈此提示。";
    }

    private static bool Https(string value) => OfficialNavigationPolicy.IsHttps(value);
}
