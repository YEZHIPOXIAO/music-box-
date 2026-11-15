using System;
using System.Collections.Generic;
using System.Linq;
using System.Threading;
using System.Threading.Tasks;
using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;
using MusicBox.Desktop.Services;

namespace MusicBox.Desktop.Views;

public sealed partial class PlaylistsPage : Page
{
    private readonly CancellationTokenSource _cts = new();
    private List<Playlist> _playlists = new();
    private List<Track> _tracks = new();

    public PlaylistsPage()
    {
        InitializeComponent();
        Loaded += async (_, _) => await RefreshAsync();
        Unloaded += (_, _) => _cts.Cancel();
    }

    private ApiClient Api => App.Api;

    private async Task RefreshAsync()
    {
        try
        {
            _playlists = (await Api.PlaylistsAsync(_cts.Token)).ToList();
            PlaylistList.ItemsSource = _playlists.Select(p => $"{p.Name}   ({p.Count})").ToList();
            Empty.Visibility = _playlists.Count == 0 ? Visibility.Visible : Visibility.Collapsed;
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

    private async void OnCreateClick(object sender, RoutedEventArgs e)
    {
        var name = NewName.Text.Trim();
        if (name.Length == 0)
        {
            Show("请输入歌单名", InfoBarSeverity.Warning);
            return;
        }
        try
        {
            await Api.CreatePlaylistAsync(name, _cts.Token);
            NewName.Text = "";
            Show($"已创建「{name}」", InfoBarSeverity.Success);
            await RefreshAsync();
        }
        catch (Exception ex)
        {
            Show(ex.Message, InfoBarSeverity.Error);
        }
    }

    private async void OnPlaylistSelected(object sender, SelectionChangedEventArgs e)
    {
        var index = PlaylistList.SelectedIndex;
        if (index < 0 || index >= _playlists.Count) return;
        try
        {
            var detail = await Api.PlaylistAsync(_playlists[index].Id, _cts.Token);
            _tracks = (detail.Tracks ?? Array.Empty<Track>()).ToList();
            TrackList.ItemsSource = _tracks
                .Select(t => $"{t.Title} — {t.ArtistText}")
                .ToList();
            Empty.Visibility = _tracks.Count == 0 ? Visibility.Visible : Visibility.Collapsed;
        }
        catch (Exception ex)
        {
            Show(ex.Message, InfoBarSeverity.Error);
        }
    }

    private async void OnTrackInvoked(object sender, ItemClickEventArgs e)
    {
        var index = TrackList.Items.IndexOf(e.ClickedItem);
        if (index < 0 || index >= _tracks.Count) return;
        var track = _tracks[index];
        try
        {
            await App.Player.PlayFromQueueAsync(_tracks, track, App.Player.Quality, _cts.Token);
            Show($"正在播放 {track.Title}", InfoBarSeverity.Informational);
        }
        catch (ApiException ex)
        {
            Show(ex.Message, InfoBarSeverity.Error);
        }
        catch (Exception ex)
        {
            Show(ex.Message, InfoBarSeverity.Error);
        }
    }
}
