using System;
using System.Collections.ObjectModel;
using System.Linq;
using System.Text.Json;
using System.Threading;
using System.Threading.Tasks;
using Microsoft.UI;
using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;
using Microsoft.UI.Xaml.Input;
using Microsoft.UI.Xaml.Media;
using MusicBox.Desktop.Services;
using Windows.System;
using Windows.UI;

namespace MusicBox.Desktop.Views;

/// <summary>Display row for one search result.</summary>
public sealed class TrackRow
{
    public required Track Track { get; init; }
    public string Title { get; init; } = "";
    public string Subtitle { get; init; } = "";
    public string ProviderName { get; init; } = "";
    public string Duration { get; init; } = "";
    public Brush BadgeBrush { get; init; } = new SolidColorBrush(Colors.Gray);

    public static TrackRow From(Track track, string providerName, string colorHex)
    {
        var album = string.IsNullOrEmpty(track.Album) ? "" : $" · {track.Album}";
        var minutes = track.Duration / 60;
        var seconds = track.Duration % 60;
        return new TrackRow
        {
            Track = track,
            Title = track.Title,
            Subtitle = $"{track.ArtistText}{album}",
            ProviderName = providerName,
            Duration = track.Duration > 0 ? $"{minutes}:{seconds:00}" : "",
            BadgeBrush = ParseBrush(colorHex),
        };
    }

    private static Brush ParseBrush(string hex)
    {
        try
        {
            var value = hex.TrimStart('#');
            if (value.Length == 6)
            {
                var r = Convert.ToByte(value[..2], 16);
                var g = Convert.ToByte(value[2..4], 16);
                var b = Convert.ToByte(value[4..], 16);
                return new SolidColorBrush(Color.FromArgb(255, r, g, b));
            }
        }
        catch (Exception) { /* fall through to the default */ }
        return new SolidColorBrush(Colors.Gray);
    }
}

public sealed partial class SearchPage : Page
{
    private readonly ObservableCollection<TrackRow> _rows = new();
    private readonly CancellationTokenSource _cts = new();
    private TrackRow? _selected;

    public SearchPage()
    {
        InitializeComponent();
        ResultList.ItemsSource = _rows;

        foreach (var (label, id) in QualityOptions)
            QualityBox.Items.Add(new ComboBoxItem { Content = label, Tag = id });
        QualityBox.SelectedIndex = 0;

        Loaded += OnLoaded;
        Unloaded += (_, _) => _cts.Cancel();
    }

    private static readonly (string Label, string Id)[] QualityOptions =
    {
        ("自动（最高可用）", "auto"),
        ("无损", "flac"),
        ("320K", "320"),
        ("128K", "128"),
    };

    private ApiClient Api => App.Api;

    private async void OnLoaded(object sender, RoutedEventArgs e)
    {
        try
        {
            var providers = await Api.ProvidersAsync(_cts.Token);
            ProviderBox.Items.Add(new ComboBoxItem { Content = "全部平台", Tag = "all" });
            foreach (var p in providers)
                ProviderBox.Items.Add(new ComboBoxItem { Content = p.Name, Tag = p.Id });
            ProviderBox.SelectedIndex = 0;
        }
        catch (Exception ex)
        {
            ShowError(ex.Message);
        }
    }

    private string CurrentProvider =>
        (ProviderBox.SelectedItem as ComboBoxItem)?.Tag as string ?? "all";

    private string CurrentQuality =>
        (QualityBox.SelectedItem as ComboBoxItem)?.Tag as string ?? "auto";

    private void OnQueryKeyDown(object sender, KeyRoutedEventArgs e)
    {
        if (e.Key == VirtualKey.Enter) _ = SearchAsync();
    }

    private async Task SearchAsync()
    {
        var query = QueryBox.Text.Trim();
        if (query.Length == 0) return;

        Busy.IsActive = true;
        StatusBar.IsOpen = false;
        EmptyState.Visibility = Visibility.Collapsed;
        try
        {
            var (tracks, errors) = await Api.SearchAsync(query, CurrentProvider, 40, _cts.Token);
            var providers = await Api.ProvidersAsync(_cts.Token);
            var meta = providers.ToDictionary(p => p.Id, p => (p.Name, p.Color));

            _rows.Clear();
            foreach (var track in tracks)
            {
                var (name, color) = meta.TryGetValue(track.Provider, out var m)
                    ? m
                    : (track.Provider, "#8b98a9");
                _rows.Add(TrackRow.From(track, name, color));
            }

            EmptyState.Visibility = _rows.Count == 0 ? Visibility.Visible : Visibility.Collapsed;
            var failed = errors.ValueKind == JsonValueKind.Array ? errors.GetArrayLength() : 0;
            StatusBar.Severity = failed > 0 ? InfoBarSeverity.Warning : InfoBarSeverity.Success;
            StatusBar.Message = failed > 0
                ? $"「{query}」共 {_rows.Count} 首（{failed} 个平台失败）"
                : $"「{query}」共 {_rows.Count} 首";
            StatusBar.IsOpen = true;
        }
        catch (OperationCanceledException) { }
        catch (Exception ex)
        {
            ShowError(ex.Message);
        }
        finally
        {
            Busy.IsActive = false;
        }
    }

    private void ShowError(string message)
    {
        StatusBar.Severity = InfoBarSeverity.Error;
        StatusBar.Message = message;
        StatusBar.IsOpen = true;
    }

    private void OnTrackInvoked(object sender, ItemClickEventArgs e)
    {
        if (e.ClickedItem is TrackRow row) Select(row);
    }

    private void Select(TrackRow row)
    {
        _selected = row;
        PlayButton.IsEnabled = true;
        AddButton.IsEnabled = true;
        NowPlaying.Text = $"♪ {row.Title} — {row.Track.ArtistText}";
    }

    private void OnPlayClick(object sender, RoutedEventArgs e)
    {
        if (_selected is null) return;
        // The ListView selection and our field can diverge; prefer the item
        // the user actually has highlighted.
        if (ResultList.SelectedItem is TrackRow row) _selected = row;
        _ = PlayAsync(_selected);
    }

    private async Task PlayAsync(TrackRow row)
    {
        var track = row.Track;
        Busy.IsActive = true;
        try
        {
            await App.Player.PlayFromQueueAsync(_rows.Select(r => r.Track), track, CurrentQuality, _cts.Token);
            NowPlaying.Text = $"♪ {track.Title} — {track.ArtistText}";
            StatusBar.IsOpen = false;
        }
        catch (ApiException ex)
        {
            ShowError(ex.Message);
            // Keep this platform/track's permission error; never substitute another recording.
        }
        catch (Exception ex)
        {
            ShowError(ex.Message);
        }
        finally
        {
            Busy.IsActive = false;
        }
    }

    private async void OnAddClick(object sender, RoutedEventArgs e)
    {
        if (ResultList.SelectedItem is TrackRow row) _selected = row;
        if (_selected is null) return;
        try
        {
            var playlists = await Api.PlaylistsAsync(_cts.Token);
            if (playlists.Length == 0)
            {
                var created = await Api.CreatePlaylistAsync("我的收藏", _cts.Token);
                await Api.AddToPlaylistAsync(created.Id, _selected.Track, _cts.Token);
                ShowInfo("已加入「我的收藏」");
                return;
            }

            var picker = new ComboBox { ItemsSource = playlists.Select(p => p.Name).ToList() };
            picker.SelectedIndex = 0;
            var dialog = new ContentDialog
            {
                Title = "加入歌单",
                Content = picker,
                PrimaryButtonText = "确定",
                CloseButtonText = "取消",
                DefaultButton = ContentDialogButton.Primary,
                XamlRoot = XamlRoot,
            };
            if (await dialog.ShowAsync() != ContentDialogResult.Primary) return;
            var target = playlists[picker.SelectedIndex];
            await Api.AddToPlaylistAsync(target.Id, _selected.Track, _cts.Token);
            ShowInfo($"已加入「{target.Name}」");
        }
        catch (Exception ex)
        {
            ShowError(ex.Message);
        }
    }

    private void ShowInfo(string message)
    {
        StatusBar.Severity = InfoBarSeverity.Success;
        StatusBar.Message = message;
        StatusBar.IsOpen = true;
    }
}
