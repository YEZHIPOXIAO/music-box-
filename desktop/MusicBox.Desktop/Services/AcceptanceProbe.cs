using System;
using System.IO;
using System.Text;
using System.Text.Json;
using System.Threading;
using System.Threading.Tasks;
using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;
using Microsoft.Web.WebView2.Core;

namespace MusicBox.Desktop.Services;

/// <summary>Opt-in native acceptance run. Uses only a scratch profile and synthetic
/// cookies/audio; never reads the user's real browser profiles or accounts.</summary>
public static class AcceptanceProbe
{
    public static async Task RunAsync(Window window, WebView2 web, string directory)
    {
        Directory.CreateDirectory(directory);
        var webReady = false;
        var domainIsolation = false;
        var released = false;
        var mediaOpened = false;
        var mediaAdvanced = false;
        var failureVisible = false;
        var paused = false;
        var resumed = false;
        var stopped = false;
        var replayed = false;
        var seeked = false;
        var muted = false;
        bool? networkMediaOpened = null;
        bool? networkMediaAdvanced = null;
        string? error = null;
        try
        {
            var profile = Path.Combine(directory, "isolated-probe-profile");
            var environment = await CoreWebView2Environment.CreateWithOptionsAsync(null, profile, new CoreWebView2EnvironmentOptions());
            await web.EnsureCoreWebView2Async(environment);
            webReady = web.CoreWebView2 is not null;
            var manager = web.CoreWebView2!.CookieManager;
            var cookie = manager.CreateCookie("probe_only", "synthetic", ".kugou.com", "/");
            cookie.IsSecure = true;
            manager.AddOrUpdateCookie(cookie);
            var own = await manager.GetCookiesAsync("https://www.kugou.com/");
            var other = await manager.GetCookiesAsync("https://www.kuwo.cn/");
            domainIsolation = own.Count == 1 && other.Count == 0;
            manager.DeleteAllCookies();
            web.Close();
            window.Content = new Grid();
            released = true;

            var wav = Path.Combine(directory, "silence.wav");
            WriteWave(wav);
            using var player = new PlayerService();
            player.SetVolume(0);
            player.PlaybackFailed += (_, _) => failureVisible = true;
            await player.PlayAsync(new Uri(wav), new Track("local", "probe", "native probe", "", "", 3, "", null), CancellationToken.None);
            mediaOpened = player.Duration >= 2;
            await Task.Delay(800);
            mediaAdvanced = player.Position > 0.1;
            player.Pause();
            await Task.Delay(150);
            var held = player.Position;
            await Task.Delay(300);
            paused = !player.IsPlaying && Math.Abs(player.Position - held) < 0.15;
            player.Resume(); await Task.Delay(250);
            resumed = player.IsPlaying && player.Position > held;
            player.Seek(1.8); await Task.Delay(150);
            seeked = player.Position >= 1.7;
            player.IsMuted = true; muted = player.IsMuted;
            player.Stop(); await Task.Delay(150);
            stopped = !player.HasSource && !player.IsPlaying && player.Position < 0.1;
            await player.PlayAsync(new Uri(wav), new Track("local", "probe", "native replay", "", "", 3, "", null), CancellationToken.None);
            await Task.Delay(250);
            replayed = player.HasSource && player.IsPlaying && player.Position > 0.1;
            try
            {
                await player.PlayAsync(new Uri(Path.Combine(directory, "absent.wav")),
                    new Track("local", "missing", "failure probe", "", "", 0, "", null), CancellationToken.None);
            }
            catch (ApiException) { /* expected: observable failure, not fake playback success */ }
            var networkUrl = Environment.GetEnvironmentVariable("MUSICBOX_ACCEPTANCE_AUDIO_URL");
            if (!string.IsNullOrEmpty(networkUrl))
            {
                await player.PlayAsync(new Uri(networkUrl),
                    new Track("network", "probe", "proxy audio", "", "", 0, "", null), CancellationToken.None);
                networkMediaOpened = player.Duration > 1;
                await Task.Delay(1000);
                networkMediaAdvanced = player.Position > 0.1;
            }
        }
        catch (Exception ex)
        {
            error = ex.GetType().Name + ": " + ex.Message;
        }
        finally
        {
            if (!released) web.Close();
            var report = new { webReady, domainIsolation, released, mediaOpened, mediaAdvanced, paused, resumed, stopped, replayed, seeked, muted, failureVisible, networkMediaOpened, networkMediaAdvanced, error };
            File.WriteAllText(Path.Combine(directory, "native-acceptance.json"), JsonSerializer.Serialize(report, new JsonSerializerOptions { WriteIndented = true }));
            window.Close();
        }
    }

    private static void WriteWave(string path)
    {
        const int rate = 44100, seconds = 3, bytes = rate * seconds * 2;
        using var writer = new BinaryWriter(File.Create(path));
        writer.Write(Encoding.ASCII.GetBytes("RIFF")); writer.Write(36 + bytes);
        writer.Write(Encoding.ASCII.GetBytes("WAVEfmt ")); writer.Write(16);
        writer.Write((short)1); writer.Write((short)1); writer.Write(rate);
        writer.Write(rate * 2); writer.Write((short)2); writer.Write((short)16);
        writer.Write(Encoding.ASCII.GetBytes("data")); writer.Write(bytes);
        writer.Write(new byte[bytes]);
    }
}
