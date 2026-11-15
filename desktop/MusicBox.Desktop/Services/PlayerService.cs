using System;
using System.Threading;
using System.Threading.Tasks;
using MusicBox.Desktop.Services;
using Windows.Media.Core;
using Windows.Media.Playback;

namespace MusicBox.Desktop.Services;

/// <summary>
/// Audio playback built on the OS media stack (Windows.Media.Playback), which
/// keeps the UI thread free and needs no third-party audio dependency.
///
/// Position/Duration/State events live on <see cref="MediaPlaybackSession"/>,
/// not on <see cref="MediaPlayer"/> — subscribing to the player for those
/// compiles nowhere, which is what previously broke the XAML codegen pass.
/// </summary>
public sealed class PlayerService : IDisposable
{
    private readonly MediaPlayer _player = new();
    private MediaSource? _currentSource;
    private ApiClient? _api;
    private CancellationTokenSource? _pending;
    private readonly SemaphoreSlim _openGate = new(1, 1);
    public PlaybackQueue Queue { get; } = new();
    public string Quality { get; private set; } = "auto";
    public string ActualQuality { get; private set; } = "unknown";
    public event EventHandler? QueueChanged;
    public event EventHandler? MediaEnded;

    public async Task PlayFromQueueAsync(System.Collections.Generic.IEnumerable<Track> tracks, Track selected, string quality, CancellationToken ct)
    {
        Queue.Replace(tracks, selected); Quality = quality;
        QueueChanged?.Invoke(this, EventArgs.Empty);
        await PlayTrackAsync(selected, quality, ct);
    }

    public async Task NextAsync(bool ended = false)
    {
        var track = Queue.Next(ended);
        QueueChanged?.Invoke(this, EventArgs.Empty);
        if (track is null) { if (ended) Stop(); return; }
        await PlayTrackAsync(track, Quality, CancellationToken.None);
    }

    public async Task PreviousAsync()
    {
        if (Position > 3 && HasSource) { Seek(0); return; }
        var track = Queue.Previous(); QueueChanged?.Invoke(this, EventArgs.Empty);
        if (track is not null) await PlayTrackAsync(track, Quality, CancellationToken.None);
    }

    public async Task SelectQueueAsync(int index)
    {
        var track = Queue.Select(index); QueueChanged?.Invoke(this, EventArgs.Empty);
        if (track is not null) await PlayTrackAsync(track, Quality, CancellationToken.None);
    }

    public async Task ChangeQualityAsync(string quality)
    {
        if (quality == Quality) return;
        var previous = Quality; var position = Position; var playing = IsPlaying;
        Quality = quality;
        if (Current is null || !HasSource) return;
        try { await PlayTrackAsync(Current, quality, CancellationToken.None); Seek(position); if (!playing) Pause(); }
        catch { Quality = previous; throw; }
    }

    public bool HasSource => _currentSource is not null;
    public bool IsPlaying => _player.PlaybackSession.PlaybackState == MediaPlaybackState.Playing;
    public bool IsMuted { get => _player.IsMuted; set => _player.IsMuted = value; }
    public string Status { get; private set; } = "未在播放";
    public event EventHandler<string>? StatusChanged;

    private void SetStatus(string status) { Status = status; StatusChanged?.Invoke(this, status); }

    public async Task PlayTrackAsync(Track track, string quality, CancellationToken ct)
    {
        _pending?.Cancel();
        var pending = CancellationTokenSource.CreateLinkedTokenSource(ct);
        _pending = pending;
        try
        {
            if (_api is null) throw new ApiException("播放器尚未连接本地服务");
            SetStatus("正在获取播放权限…");
            var info = await _api.StreamAsync(track.Provider, track.Id, StreamRequest.FromTrack(track, quality), pending.Token);
            pending.Token.ThrowIfCancellationRequested();
            ActualQuality = info.Quality;
            await PlayAsync(_api.Absolute(info.Src), track, pending.Token);
            SetStatus("正在播放 · 实际音质 " + info.Quality);
        }
        catch (OperationCanceledException)
        {
            if (_pending == pending && Status != "已停止") SetStatus("已取消");
            throw;
        }
        catch (Exception ex)
        {
            SetStatus("播放失败");
            PlaybackFailed?.Invoke(this, ex.Message);
            throw;
        }
        finally
        {
            if (_pending == pending) _pending = null;
            pending.Dispose();
        }
    }

    public event EventHandler<Track?>? TrackChanged;
    public event EventHandler<double>? PositionChanged;
    public event EventHandler<double>? DurationChanged;
    public event EventHandler<bool>? PlaybackStateChanged;
    public event EventHandler<string>? PlaybackFailed;

    public Track? Current { get; private set; }

    public double Position => _player.PlaybackSession.Position.TotalSeconds;

    public double Duration
    {
        get
        {
            var total = _player.PlaybackSession.NaturalDuration.TotalSeconds;
            return double.IsNaN(total) || total < 0 ? 0 : total;
        }
    }

    public PlayerService()
    {
        var session = _player.PlaybackSession;
        session.PositionChanged += (_, _) => PositionChanged?.Invoke(this, Position);
        session.NaturalDurationChanged += (_, _) => DurationChanged?.Invoke(this, Duration);
        session.PlaybackStateChanged += (_, _) =>
            PlaybackStateChanged?.Invoke(this,
                session.PlaybackState == MediaPlaybackState.Playing);
        // Without this the UI sits on "playing" forever when the proxy answers
        // 403/424; the failure was previously discarded entirely.
        _player.MediaFailed += (_, e) =>
            PlaybackFailed?.Invoke(this,
                string.IsNullOrWhiteSpace(e.ErrorMessage) ? "播放失败" : e.ErrorMessage);
        _player.MediaEnded += (_, _) => MediaEnded?.Invoke(this, EventArgs.Empty);
        _player.CommandManager.IsEnabled = false;
    }

    public void Attach(ApiClient api) => _api = api;

    public async Task PlayAsync(Uri source, Track track, CancellationToken ct)
    {
        ct.ThrowIfCancellationRequested();
        await _openGate.WaitAsync(ct);
        try
        {
        Current = track;
        SetStatus("正在打开音频…");
        var next = MediaSource.CreateFromUri(source);
        // Dispose the previous source: it holds an open HTTP stream, and GC is
        // too lazy for a user who skips through a dozen tracks.
        var previous = _currentSource;
        _currentSource = next;
        var opened = new TaskCompletionSource<bool>(TaskCreationOptions.RunContinuationsAsynchronously);
        Windows.Foundation.TypedEventHandler<MediaPlayer, object> onOpened = (_, _) => opened.TrySetResult(true);
        Windows.Foundation.TypedEventHandler<MediaPlayer, MediaPlayerFailedEventArgs> onFailed = (_, e) =>
            opened.TrySetException(new ApiException("音频打开失败：" + e.ErrorMessage));
        _player.MediaOpened += onOpened;
        _player.MediaFailed += onFailed;
        try
        {
            _player.Source = next;
            previous?.Dispose();
            _player.Play();
            await opened.Task.WaitAsync(TimeSpan.FromSeconds(20), ct);
            ct.ThrowIfCancellationRequested();
            SetStatus("正在播放");
        }
        catch
        {
            if (_currentSource == next)
            {
                _player.Pause(); _player.Source = null;
                _currentSource = null; next.Dispose();
            }
            throw;
        }
        finally
        {
            _player.MediaOpened -= onOpened;
            _player.MediaFailed -= onFailed;
        }
        TrackChanged?.Invoke(this, track);

        if (_api is not null)
        {
            try { await _api.AddHistoryAsync(track, ct).ConfigureAwait(false); }
            catch (Exception) { /* history is optional; never block playback */ }
        }
        }
        finally { _openGate.Release(); }
    }

    public void Pause()
    {
        if (!HasSource) return;
        _player.Pause(); SetStatus("已暂停");
    }

    public void Resume()
    {
        if (!HasSource) return;
        _player.Play(); SetStatus("正在播放");
    }

    public void Stop()
    {
        _pending?.Cancel();
        _player.Pause(); _player.Source = null;
        var source = _currentSource; _currentSource = null; source?.Dispose();
        SetStatus("已停止");
        PositionChanged?.Invoke(this, 0); DurationChanged?.Invoke(this, 0);
        PlaybackStateChanged?.Invoke(this, false);
    }

    public void Toggle()
    {
        if (IsPlaying) Pause(); else Resume();
    }

    public void Seek(double seconds)
    {
        // Seeking before MediaOpened would clamp everything to 0 and lose the
        // user's position, so ignore it until the duration is known.
        if (Duration <= 0) return;
        _player.PlaybackSession.Position = TimeSpan.FromSeconds(
            Math.Clamp(seconds, 0, Duration));
    }

    public void SetVolume(double percent) => _player.Volume = Math.Clamp(percent / 100, 0, 1);

    public void Dispose()
    {
        _pending?.Cancel();
        _player.Pause();
        _player.Source = null;
        _currentSource?.Dispose();
        _currentSource = null;
        _player.Dispose();
    }
}
