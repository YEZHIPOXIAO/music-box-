using System;
using System.Text.Json;
using System.Threading;
using System.Threading.Tasks;
using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;
using MusicBox.Desktop.Services;

namespace MusicBox.Desktop.Views;

public sealed partial class PrivacyPage : Page
{
    private readonly CancellationTokenSource _cts = new();
    private string _encryption = "";

    public PrivacyPage()
    {
        InitializeComponent();
        Loaded += async (_, _) => await RefreshAsync();
        Unloaded += (_, _) => _cts.Cancel();
    }

    private async Task RefreshAsync()
    {
        try
        {
            var health = await App.Api.HealthAsync(_cts.Token);
            var privacy = health.GetProperty("privacy");
            var scheme = privacy.TryGetProperty("encryptionScheme", out var s)
                ? s.GetString()
                : (privacy.TryGetProperty("encryptedAccounts", out var e) && e.GetBoolean()
                    ? "fernet"
                    : "plain");
            _encryption = scheme switch
            {
                "fernet" => "Fernet（AES-128-CBC + HMAC-SHA256）加密保存在本机。",
                "hmac" => "HMAC-SHA256 加密保存在本机（未安装 cryptography，使用内置方案）。",
                _ => "未加密（由 MUSICBOX_NO_ENCRYPT 关闭）。",
            };
            EncryptionText.Text = _encryption;

            var home = privacy.TryGetProperty("dataDir", out var d) ? d.GetString() : null;
            PathText.Text = home ?? "(数据目录由 MUSICBOX_HOME 决定，默认在用户配置目录下)";
        }
        catch (Exception ex)
        {
            Show(ex.Message, InfoBarSeverity.Error);
        }
    }

    private void Show(string message, InfoBarSeverity severity)
    {
        StatusBar.Severity = severity;
        StatusBar.Message = message;
        StatusBar.IsOpen = true;
    }

    private async void OnWipeClick(object sender, RoutedEventArgs e)
    {
        var confirm = new ContentDialog
        {
            Title = "清空全部本地数据？",
            Content = "账号、歌单、播放历史都会被删除，无法恢复。",
            PrimaryButtonText = "清空",
            CloseButtonText = "取消",
            DefaultButton = ContentDialogButton.Close,
            XamlRoot = XamlRoot,
        };
        if (await confirm.ShowAsync() != ContentDialogResult.Primary) return;

        try
        {
            using var http = new System.Net.Http.HttpClient();
            var body = new System.Net.Http.StringContent(
                JsonSerializer.Serialize(new { confirm = true }),
                System.Text.Encoding.UTF8, "application/json");
            var resp = await http.PostAsync(App.Api.Absolute("api/privacy/wipe"), body, _cts.Token);
            if (!resp.IsSuccessStatusCode)
            {
                Show($"清空失败：HTTP {(int)resp.StatusCode}", InfoBarSeverity.Error);
                return;
            }
            Show("本地数据已清空", InfoBarSeverity.Success);
        }
        catch (Exception ex)
        {
            Show(ex.Message, InfoBarSeverity.Error);
        }
    }
}
