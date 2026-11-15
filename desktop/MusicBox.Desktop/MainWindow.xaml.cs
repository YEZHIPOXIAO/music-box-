using System;
using System.IO;
using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;
using Microsoft.UI.Xaml.Controls.Primitives;
using Microsoft.UI.Xaml.Input;

namespace MusicBox.Desktop;

public sealed partial class MainWindow : Window
{
    private bool _dragging;
    private bool _transportReady;
    private bool _changingTrack;
    private Views.DesktopLyricsWindow? _lyricsWindow;
    private System.Threading.CancellationTokenSource? _lyricsCts;
    private MusicBox.Desktop.Services.LyricLine[] _lyrics = Array.Empty<MusicBox.Desktop.Services.LyricLine>();
    private string _lyricStatus = "等待歌词";
    /// <summary>Set while the code below writes Progress, so those writes do
    /// not look like user seeks. Without it, changing Maximum clamps Value and
    /// fires ValueChanged, which used to yank a freshly started track back to
    /// its first second.</summary>
    private bool _suppressSeek;

    public MainWindow()
    {
        InitializeComponent();
        Title = "MusicBox";
        ExtendsContentIntoTitleBar = true;
        SetTitleBar(AppTitleBar);

        AppWindow.Resize(new Windows.Graphics.SizeInt32(1220, 820));
        AppWindow.TitleBar.PreferredHeightOption =
            Microsoft.UI.Windowing.TitleBarHeightOption.Tall;
        // Custom title bars do not reserve room for the caption buttons.
        AppTitleBar.Padding = new Thickness(16, 0, AppWindow.TitleBar.RightInset + 16, 0);

        // Selecting the first item already navigates (SelectionChanged is wired
        // in XAML); navigating again built SearchPage twice and double-fired
        // the provider request.
        Nav.SelectedItem = Nav.MenuItems[0];

        HookPlayer();
        HookProgressBar();

        Volume.Value = 85;  // raises ValueChanged, which applies the volume
        PlaybackModeBox.SelectedIndex = 0; PlayerQualityBox.SelectedIndex = 0;
        _transportReady = true;
        App.Player.MediaEnded += (_, _) => DispatcherQueue.TryEnqueue(async () => await TransportAsync(() => App.Player.NextAsync(true)));
        Closed += (_, _) => { _lyricsCts?.Cancel(); _lyricsWindow?.Close(); };
    }

    public async System.Threading.Tasks.Task ProbeOfficialLoginAsync(string directory)
    {
        var page = new Views.AccountsPage();
        var ready = new System.Threading.Tasks.TaskCompletionSource<bool>();
        page.Loaded += (_, _) => ready.TrySetResult(true);
        ContentFrame.Content = page;
        await ready.Task;
        await page.RunOfficialLoginProbeAsync(directory);
    }

    public async System.Threading.Tasks.Task CaptureTransportAsync(string directory)
    {
        System.IO.Directory.CreateDirectory(directory);
        await System.Threading.Tasks.Task.Delay(700);
        var bitmap = new Microsoft.UI.Xaml.Media.Imaging.RenderTargetBitmap();
        await bitmap.RenderAsync(RootGrid);
        var pixels = await bitmap.GetPixelsAsync();
        using var file = System.IO.File.Create(System.IO.Path.Combine(directory, "player-window.png"));
        using var stream = file.AsRandomAccessStream();
        var encoder = await Windows.Graphics.Imaging.BitmapEncoder.CreateAsync(Windows.Graphics.Imaging.BitmapEncoder.PngEncoderId, stream);
        encoder.SetPixelData(Windows.Graphics.Imaging.BitmapPixelFormat.Bgra8, Windows.Graphics.Imaging.BitmapAlphaMode.Premultiplied,
            (uint)bitmap.PixelWidth, (uint)bitmap.PixelHeight, 96, 96, System.Runtime.InteropServices.WindowsRuntime.WindowsRuntimeBufferExtensions.ToArray(pixels));
        await encoder.FlushAsync();
        System.IO.File.WriteAllText(System.IO.Path.Combine(directory, "transport-layout.json"), System.Text.Json.JsonSerializer.Serialize(new
        {
            windowWidth = RootGrid.ActualWidth, playWidth = PlayPauseButton.ActualWidth,
            stopWidth = StopButton.ActualWidth, controlsVisible = PlayPauseButton.ActualWidth > 80 && StopButton.ActualWidth > 50,
            text = PlayButtonText.Text
        }));
        Close();
    }

    public async System.Threading.Tasks.Task ProbeConsoleAsync(string directory)
    {
        Directory.CreateDirectory(directory);
        var results = new System.Collections.Generic.Dictionary<string, object?>();
        try
        {
            var a = new Services.Track("local", "A", "Queue A", "fixture", "", 4, "", null);
            var b = a with { Id = "B", Title = "Queue B" };
            var c = a with { Id = "C", Title = "Queue C" };
            App.Player.SetVolume(0);
            await App.Player.PlayFromQueueAsync(new[] { a, b, c }, a, "128", System.Threading.CancellationToken.None);
            await App.Player.NextAsync(); results["next"] = App.Player.Current?.Id == "B";
            await App.Player.PreviousAsync(); results["previous"] = App.Player.Current?.Id == "A";
            App.Player.Queue.Mode = Services.PlaybackMode.RepeatAll;
            await App.Player.SelectQueueAsync(2); await App.Player.NextAsync();
            results["listWrap"] = App.Player.Current?.Id == "A";
            App.Player.Queue.Mode = Services.PlaybackMode.Shuffle;
            await App.Player.NextAsync(); results["shuffle"] = App.Player.Current?.Id != "A";
            await App.Player.PreviousAsync(); results["shuffleHistory"] = App.Player.Current?.Id == "A";
            App.Player.Seek(1); App.Player.Pause();
            await App.Player.ChangeQualityAsync("320");
            results["qualityPreservesPosition"] = App.Player.Quality == "320" && !App.Player.IsPlaying && App.Player.Position >= 0.9;
            var lyricsReady = App.Api.LyricsAsync("local", "A", System.Threading.CancellationToken.None);
            _lyrics = (await lyricsReady).Lines ?? Array.Empty<Services.LyricLine>();
            _lyricsWindow = new Views.DesktopLyricsWindow(); _lyricsWindow.Activate();
            UpdateLyrics(1.5);
            results["desktopLyrics"] = _lyricsWindow.CurrentText == "second fixture line";
            _lyricsWindow.Close(); _lyricsWindow = null;
            App.Player.Queue.Mode = Services.PlaybackMode.RepeatOne;
            App.Player.Resume(); App.Player.Seek(3.5);
            await System.Threading.Tasks.Task.Delay(1600);
            results["naturalSingleLoop"] = App.Player.Current?.Id == "A" && App.Player.IsPlaying && App.Player.Position < 2.5;
            App.Player.Queue.Mode = Services.PlaybackMode.Sequential;
            App.Player.Seek(3.5); await System.Threading.Tasks.Task.Delay(1600);
            results["naturalNext"] = App.Player.Current?.Id == "B" && App.Player.IsPlaying;
            App.Player.Stop(); results["stopped"] = !App.Player.HasSource;
            var pending = App.Player.PlayTrackAsync(a with { Id = "DELAY" }, "128", System.Threading.CancellationToken.None);
            await System.Threading.Tasks.Task.Delay(150); App.Player.Stop();
            try { await pending; results["stopCancelsPending"] = false; }
            catch (OperationCanceledException) { results["stopCancelsPending"] = !App.Player.HasSource && App.Player.Status == "已停止"; }
        }
        catch (Exception ex) { results["error"] = ex.GetType().Name + ": " + ex.Message; }
        File.WriteAllText(Path.Combine(directory, "console-runtime.json"), System.Text.Json.JsonSerializer.Serialize(results));
        await CaptureTransportAsync(directory);
    }

    // ---- player wiring ----------------------------------------------------
    private void HookPlayer()
    {
        var player = App.Player;

        player.TrackChanged += (_, track) => DispatcherQueue.TryEnqueue(() =>
        {
            if (track is null) return;
            NowTitle.Text = track.Title;
            NowArtist.Text = track.ArtistText;
            TitleBarSubtitle.Text = $"{track.Title} — {track.ArtistText}";
            if (_lyricsWindow is not null) _ = LoadLyricsAsync(track);
            var tags = new[] { "auto", "128", "320", "flac" };
            var selected = Array.IndexOf(tags, player.Quality);
            if (selected >= 0) { _transportReady = false; PlayerQualityBox.SelectedIndex = selected; _transportReady = true; }
        });

        player.PositionChanged += (_, seconds) => DispatcherQueue.TryEnqueue(() =>
        {
            UpdateLyrics(seconds);
            if (_dragging) return;
            PosText.Text = Format(seconds);
            if (player.Duration > 0.5) SetProgressSilently(seconds, player.Duration);
        });

        player.DurationChanged += (_, total) => DispatcherQueue.TryEnqueue(() =>
        {
            DurText.Text = Format(total);
            SetProgressSilently(Progress.Value, Math.Max(total, 1));
        });

        player.PlaybackStateChanged += (_, playing) => DispatcherQueue.TryEnqueue(() =>
        {
            PlayIcon.Glyph = playing ? "\uE769" : "\uE768";
            PlayButtonText.Text = playing ? "暂停" : "播放";
        });
        player.StatusChanged += (_, status) => DispatcherQueue.TryEnqueue(() => TransportStatus.Text = status);

        // Media failures were previously swallowed: the button said "playing"
        // and nothing else happened, with no way to find out why.
        player.PlaybackFailed += (_, message) => DispatcherQueue.TryEnqueue(() =>
        {
            PlayIcon.Glyph = "\uE768";
            NowArtist.Text = $"播放失败：{message}";
        });
    }

    private void HookProgressBar()
    {
        // Slider swallows pointer events from the element tree, so the handler
        // must be attached with handledEventsToo: true to see them at all.
        Progress.AddHandler(UIElement.PointerPressedEvent,
            new PointerEventHandler((_, _) => _dragging = true), handledEventsToo: true);
        Progress.AddHandler(UIElement.PointerReleasedEvent,
            new PointerEventHandler((_, _) => CommitSeek()), handledEventsToo: true);
        Progress.AddHandler(UIElement.PointerCaptureLostEvent,
            new PointerEventHandler((_, _) => CommitSeek()), handledEventsToo: true);
    }

    private void CommitSeek()
    {
        if (!_dragging) return;
        _dragging = false;
        App.Player.Seek(Progress.Value);
    }

    private void SetProgressSilently(double value, double maximum)
    {
        _suppressSeek = true;
        try
        {
            Progress.Maximum = maximum;
            Progress.Value = Math.Clamp(value, 0, maximum);
        }
        finally
        {
            _suppressSeek = false;
        }
    }

    private static string Format(double seconds)
    {
        if (double.IsNaN(seconds) || seconds < 0) return "0:00";
        var total = (int)seconds;
        return total >= 3600
            ? $"{total / 3600}:{total % 3600 / 60:00}:{total % 60:00}"
            : $"{total / 60}:{total % 60:00}";
    }

    // ---- commands ---------------------------------------------------------
    private void OnNavSelectionChanged(NavigationView sender, NavigationViewSelectionChangedEventArgs args)
    {
        if (args.SelectedItem is not NavigationViewItem item) return;
        var target = item.Tag?.ToString() switch
        {
            "playlists" => typeof(Views.PlaylistsPage),
            "accounts" => typeof(Views.AccountsPage),
            "privacy" => typeof(Views.PrivacyPage),
            _ => typeof(Views.SearchPage),
        };
        // Re-navigating to the current page throws away search text and results.
        if (ContentFrame.CurrentSourcePageType == target) return;
        ContentFrame.Navigate(target);
    }

    private async System.Threading.Tasks.Task TransportAsync(Func<System.Threading.Tasks.Task> action)
    {
        if (_changingTrack) return;
        _changingTrack = true;
        try { await action(); }
        catch (OperationCanceledException) { }
        catch (Exception ex) { NowArtist.Text = ex.Message; }
        finally { _changingTrack = false; }
    }

    private async void OnPreviousClick(object sender, RoutedEventArgs e) => await TransportAsync(() => App.Player.PreviousAsync());
    private async void OnNextClick(object sender, RoutedEventArgs e) => await TransportAsync(() => App.Player.NextAsync());
    private void OnPlaybackModeChanged(object sender, SelectionChangedEventArgs e)
    {
        if (!_transportReady) return;
        App.Player.Queue.Mode = (Services.PlaybackMode)PlaybackModeBox.SelectedIndex;
    }
    private async void OnPlayerQualityChanged(object sender, SelectionChangedEventArgs e)
    {
        if (!_transportReady || PlayerQualityBox.SelectedItem is not ComboBoxItem { Tag: string quality }) return;
        await TransportAsync(() => App.Player.ChangeQualityAsync(quality));
        var tags = new[] { "auto", "128", "320", "flac" };
        _transportReady = false; PlayerQualityBox.SelectedIndex = Array.IndexOf(tags, App.Player.Quality); _transportReady = true;
    }
    private void OnQueueClick(object sender, RoutedEventArgs e)
    {
        var list = new ListView { Width = 460, MaxHeight = 360, IsItemClickEnabled = true, SelectionMode = ListViewSelectionMode.Single };
        void Refresh()
        {
            list.ItemsSource = System.Linq.Enumerable.ToArray(System.Linq.Enumerable.Select(App.Player.Queue.Items,
                (t, i) => $"{(i == App.Player.Queue.Index ? "▶ " : "")}{i+1}. {t.Title} — {t.ArtistText} [{t.Provider}]"));
            list.SelectedIndex = App.Player.Queue.Index;
        }
        Refresh();
        var panel = new StackPanel { Spacing = 8 };
        panel.Children.Add(new TextBlock { Text = "当前播放队列 · " + App.Player.Queue.Items.Count });
        panel.Children.Add(list);
        var remove = new Button { Content = "移除所选（当前曲目保留）" };
        remove.Click += (_, _) => { App.Player.Queue.Remove(list.SelectedIndex); Refresh(); };
        var clear = new Button { Content = "清空其他曲目" };
        clear.Click += (_, _) => { App.Player.Queue.KeepCurrent(); Refresh(); };
        panel.Children.Add(remove); panel.Children.Add(clear);
        var flyout = new Flyout { Content = panel };
        list.ItemClick += async (_, args) =>
        {
            var index = list.Items.IndexOf(args.ClickedItem); flyout.Hide();
            await TransportAsync(() => App.Player.SelectQueueAsync(index));
        };
        flyout.ShowAt((FrameworkElement)sender);
    }

    private async void OnDesktopLyricsClick(object sender, RoutedEventArgs e)
    {
        if (_lyricsWindow is not null) { _lyricsWindow.Activate(); return; }
        var window = new Views.DesktopLyricsWindow(); _lyricsWindow = window;
        window.Closed += (_, _) => { _lyricsWindow = null; _lyricsCts?.Cancel(); };
        window.Activate();
        if (App.Player.Current is not null) await LoadLyricsAsync(App.Player.Current);
    }
    private async System.Threading.Tasks.Task LoadLyricsAsync(Services.Track track)
    {
        _lyricsCts?.Cancel(); _lyricsCts = new System.Threading.CancellationTokenSource();
        var token = _lyricsCts.Token; _lyrics = Array.Empty<Services.LyricLine>(); _lyricStatus = "正在获取歌词…";
        UpdateLyrics(0);
        try
        {
            var result = await App.Api.LyricsAsync(track.Provider, track.Id, token);
            token.ThrowIfCancellationRequested();
            _lyrics = result.Lines ?? Array.Empty<Services.LyricLine>();
            _lyricStatus = _lyrics.Length == 0 ? "平台未提供歌词" : track.Title;
            UpdateLyrics(App.Player.Position);
        }
        catch (OperationCanceledException) { }
        catch (Exception) { if (!token.IsCancellationRequested) { _lyricStatus = "歌词暂不可用"; UpdateLyrics(0); } }
    }
    private void UpdateLyrics(double seconds)
    {
        if (_lyricsWindow is null) return;
        var index = -1;
        for (var i = 0; i < _lyrics.Length && _lyrics[i].Time <= seconds; i++) index = i;
        _lyricsWindow.Update(index >= 0 ? _lyrics[index].Text : _lyricStatus,
            index+1 < _lyrics.Length ? _lyrics[index+1].Text : "");
    }

    private async void OnPlayPauseClick(object sender, RoutedEventArgs e)
    {
        if (App.Player.HasSource) { App.Player.Toggle(); return; }
        if (App.Player.Current is null) { TransportStatus.Text = "请先选择歌曲"; return; }
        try { await App.Player.PlayTrackAsync(App.Player.Current, App.Player.Quality, System.Threading.CancellationToken.None); }
        catch (OperationCanceledException) { }
        catch (Exception ex) { NowArtist.Text = ex.Message; }
    }

    private void OnStopClick(object sender, RoutedEventArgs e)
    {
        App.Player.Stop(); PlayButtonText.Text = "播放";
        SetProgressSilently(0, 1); PosText.Text = "0:00"; DurText.Text = "0:00";
    }

    private async void OnReplayClick(object sender, RoutedEventArgs e)
    {
        if (App.Player.Current is null) { TransportStatus.Text = "请先选择歌曲"; return; }
        if (App.Player.HasSource) { App.Player.Seek(0); App.Player.Resume(); return; }
        try { await App.Player.PlayTrackAsync(App.Player.Current, App.Player.Quality, System.Threading.CancellationToken.None); }
        catch (OperationCanceledException) { }
        catch (Exception ex) { NowArtist.Text = ex.Message; }
    }

    private void OnMuteClick(object sender, RoutedEventArgs e)
    {
        App.Player.IsMuted = !App.Player.IsMuted;
        ToolTipService.SetToolTip(MuteButton, App.Player.IsMuted ? "已静音，点击恢复" : "静音");
        MuteButton.Opacity = App.Player.IsMuted ? 0.45 : 1;
    }

    private void OnSeekChanged(object sender, RangeBaseValueChangedEventArgs e)
    {
        // Ignore writes this window made itself; a real seek is committed on
        // pointer release, never per tick.
        if (_suppressSeek || _dragging) return;
        App.Player.Seek(e.NewValue);
    }

    private void OnVolumeChanged(object sender, RangeBaseValueChangedEventArgs e) =>
        App.Player.SetVolume(e.NewValue);

    // ---- backend status ---------------------------------------------------
    public void SetStatus(string message) => TitleBarSubtitle.Text = message;

    public async System.Threading.Tasks.Task ShowBackendErrorAsync(string message)
    {
        TitleBarSubtitle.Text = "后端未启动";
        var dialog = new ContentDialog
        {
            Title = "无法启动本地服务",
            Content = new ScrollViewer
            {
                MaxHeight = 320,
                Content = new TextBlock
                {
                    Text = message,
                    TextWrapping = TextWrapping.Wrap,
                    IsTextSelectionEnabled = true,
                },
            },
            CloseButtonText = "知道了",
            XamlRoot = Content.XamlRoot,
        };
        try
        {
            await dialog.ShowAsync();
        }
        catch (Exception)
        {
            // Showing the dialog must never take the window down with it.
        }
    }
}
