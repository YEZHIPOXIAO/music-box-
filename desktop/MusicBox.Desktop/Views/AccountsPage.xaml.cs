using System;
using System.IO;
using System.Collections.Generic;
using System.Text.Json;
using System.Linq;
using System.Threading;
using System.Threading.Tasks;
using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;
using Microsoft.UI.Xaml.Media.Imaging;
using Microsoft.Web.WebView2.Core;
using MusicBox.Desktop.Services;

namespace MusicBox.Desktop.Views;

/// <summary>
/// Account binding through each platform's own official webpage login choices.
/// A resizable owned window hosts WebView2; credentials are read after consent.
/// </summary>
public sealed partial class AccountsPage : Page
{
    private CancellationTokenSource _cts = new();
    private OfficialLoginWindow? _loginWindow;
    private bool _loaded;

    public AccountsPage()
    {
        InitializeComponent();
        Loaded += OnLoaded;
        Unloaded += (_, _) =>
        {
            _loaded = false;
            _cts.Cancel();
            _loginWindow?.Close();
        };
    }

    private ApiClient Api => App.Api;

    private async void OnLoaded(object sender, RoutedEventArgs e)
    {
        if (_cts.IsCancellationRequested) { _cts.Dispose(); _cts = new(); }
        _loaded = true;
        await RefreshAsync();
    }

    private async Task RefreshAsync()
    {
        try
        {
            var providers = await Api.ProvidersAsync(_cts.Token);
            foreach (var p in providers)
            {
                var bound = p.LoggedIn;
                switch (p.Id)
                {
                    case "qq":
                        QQStateText.Text = bound ? "凭证已绑定" : "未绑定";
                        break;
                    case "kugou":
                        KugouState.Text = bound ? "已绑定" : "未绑定";
                        break;
                    case "kuwo":
                        KuwoState.Text = bound ? "已绑定" : "未绑定";
                        break;
                }
            }
        }
        catch (Exception ex)
        {
            Show(ex.Message, InfoBarSeverity.Error);
        }
    }

    private void Show(string message, InfoBarSeverity severity)
    {
        if (!_loaded) return;
        StatusBar.Severity = severity;
        StatusBar.Message = message;
        StatusBar.IsOpen = true;
    }

    // ------------------------------------------------ WebView login
    private Dictionary<string, object?>? _probeResult;

    private async void OnWebLoginClick(object sender, RoutedEventArgs e)
    {
        if (sender is Button { Tag: string provider }) await OpenOfficialLoginAsync(provider);
    }

    internal async Task RunOfficialLoginProbeAsync(string directory)
    {
        Directory.CreateDirectory(directory);
        var results = new List<Dictionary<string, object?>>();
        foreach (var provider in new[] { "qq", "kugou", "kuwo" })
        {
            _probeResult = new Dictionary<string, object?> { ["provider"] = provider, ["authenticated"] = false };
            await OpenOfficialLoginAsync(provider);
            results.Add(_probeResult);
            _probeResult = null;
        }
        File.WriteAllText(Path.Combine(directory, "official-login-dialogs.json"), JsonSerializer.Serialize(results,
            new JsonSerializerOptions { WriteIndented = true }));
        App.MainWindowInstance?.Close();
    }

    private async Task OpenOfficialLoginAsync(string provider)
    {
        if (_loginWindow is not null) { _loginWindow.Activate(); return; }
        try
        {
            var login = new OfficialLoginWindow(provider) { Probe = _probeResult };
            _loginWindow = login;
            login.Activate();
            var bound = await login.Completion;
            if (bound && _loaded)
            {
                Show("平台凭证已绑定；会员权限以实际播放结果为准", InfoBarSeverity.Success);
                await RefreshAsync();
            }
        }
        catch (Exception ex)
        {
            if (_probeResult is not null) _probeResult["error"] = ex.GetType().Name + ": " + ex.Message;
            Show("打开登录窗口失败：" + ex.Message, InfoBarSeverity.Error);
        }
        finally { _loginWindow = null; }
    }

    private async void OnVerifyMembershipClick(object sender, RoutedEventArgs e)
    {
        if (sender is not Button { Tag: string provider } button) return;
        button.IsEnabled = false;
        MembershipResult.Text = "正在向平台检测账号与音乐会员…";
        try
        {
            var result = await Api.VerifyAccountAsync(provider, _cts.Token);
            var verified = result.GetProperty("membershipVerified").GetBoolean();
            var vip = result.GetProperty("isVip");
            var label = verified ? vip.ValueKind == JsonValueKind.True ? "会员已验证" : "本次查询的会员产品未激活" : "会员状态未知";
            MembershipResult.Text = label + "：" + result.GetProperty("message").GetString()
                + "\n免费听歌剩余时长：平台未明确返回。会员状态不作为本地播放门槛。";
        }
        catch (OperationCanceledException) { }
        catch (Exception ex) { MembershipResult.Text = "检测失败，会员状态未知：" + ex.Message; }
        finally { button.IsEnabled = true; }
    }

    private async void OnProviderUnbindClick(object sender, RoutedEventArgs e)
    {
        if (sender is not Button { Tag: string provider }) return;
        try
        {
            await Api.LogoutAsync(provider, _cts.Token);
            Show("已解绑平台凭证", InfoBarSeverity.Success);
            await RefreshAsync();
        }
        catch (OperationCanceledException) { }
        catch (Exception ex) { Show(ex.Message, InfoBarSeverity.Error); }
    }

    private async void OnBackupConfigClick(object sender, RoutedEventArgs e)
    {
        if (sender is not Button { Tag: string provider }) return;
        try
        {
            var source = await Api.PlaybackSourceAsync(provider, _cts.Token);
            var mode = new ComboBox();
            mode.Items.Add("官方接入（默认）");
            mode.Items.Add("本机备用 API");
            mode.SelectedIndex = source.GetProperty("mode").GetString() == "backup" ? 1 : 0;
            var address = new TextBox { Header = "本机服务地址", PlaceholderText = "http://127.0.0.1:3000", Text = source.GetProperty("baseUrl").GetString() ?? "" };
            var path = new TextBox { Header = "播放接口路径", Text = source.GetProperty("path").GetString() ?? "" };
            var feedback = new TextBlock { Text = source.GetProperty("note").GetString(), TextWrapping = TextWrapping.Wrap, MaxWidth = 500 };
            var panel = new StackPanel { Spacing = 12 };
            panel.Children.Add(new TextBlock { Text = source.GetProperty("repository").GetString(), IsTextSelectionEnabled = true });
            panel.Children.Add(mode); panel.Children.Add(address); panel.Children.Add(path); panel.Children.Add(feedback);
            var dialog = new ContentDialog { Title = "备用播放接入 · " + provider, Content = panel, PrimaryButtonText = "保存切换", CloseButtonText = "取消", XamlRoot = XamlRoot };
            dialog.PrimaryButtonClick += async (_, args) =>
            {
                var deferral = args.GetDeferral();
                args.Cancel = true;
                try
                {
                    await Api.SetPlaybackSourceAsync(provider, mode.SelectedIndex == 1 ? "backup" : "official", address.Text.Trim(), path.Text.Trim(), _cts.Token);
                    args.Cancel = false;
                    Show("已单独切换 " + provider + " 播放接入；其他平台保持原设置", InfoBarSeverity.Success);
                }
                catch (Exception ex) { feedback.Text = ex.Message; }
                finally { deferral.Complete(); }
            };
            await dialog.ShowAsync();
        }
        catch (Exception ex) { Show(ex.Message, InfoBarSeverity.Error); }
    }

    // ------------------------------------------------ manual paste
    private async void OnManualSaveClick(object sender, RoutedEventArgs e)
    {
        if (sender is not Button { Tag: string provider }) return;
        var box = provider switch
        {
            "qq" => CookieQQ,
            "kugou" => CookieKugou,
            _ => CookieKuwo,
        };
        var value = box.Password.Trim();
        if (value.Length == 0)
        {
            Show("请先粘贴 Cookie", InfoBarSeverity.Warning);
            return;
        }
        try
        {
            await Api.SaveCookieAsync(provider, value, _cts.Token);
            box.Password = "";
            Show("已保存", InfoBarSeverity.Success);
            await RefreshAsync();
        }
        catch (Exception ex)
        {
            Show(ex.Message, InfoBarSeverity.Error);
        }
    }
}
