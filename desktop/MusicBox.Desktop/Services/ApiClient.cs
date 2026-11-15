using System;
using System.Collections.Generic;
using System.Linq;
using System.Net.Http;
using System.Net.Http.Json;
using System.Text.Json;
using System.Text.Json.Serialization;
using System.Threading;
using System.Threading.Tasks;

namespace MusicBox.Desktop.Services;

/// <summary>Conversions between the DTOs and the backend's request shapes.</summary>
public static class TrackShape
{
    /// <summary>Artist list for the backend, derived from the display text.</summary>
    public static string[] ArtistsOrSplit(Track track) => track.Artists is { Length: > 0 }
        ? track.Artists
        : (track.ArtistText ?? "")
            .Split('/', StringSplitOptions.RemoveEmptyEntries)
            .Select(a => a.Trim())
            .Where(a => a.Length > 0)
            .ToArray();
}

public sealed record Track(
    [property: JsonPropertyName("provider")] string Provider,
    [property: JsonPropertyName("id")] string Id,
    [property: JsonPropertyName("title")] string Title,
    [property: JsonPropertyName("artistText")] string ArtistText,
    [property: JsonPropertyName("album")] string Album,
    [property: JsonPropertyName("duration")] int Duration,
    [property: JsonPropertyName("cover")] string Cover,
    [property: JsonPropertyName("extra")] Dictionary<string, JsonElement>? Extra,
    // The backend's TrackPayload declares `artists: list[str]`, not artistText,
    // and Pydantic ignores unknown fields. Posting only artistText silently
    // stored playlists with no artist at all, so it was always sent too.
    [property: JsonPropertyName("artists")] string[]? Artists = null);

public sealed record ProviderInfo(
    [property: JsonPropertyName("id")] string Id,
    [property: JsonPropertyName("name")] string Name,
    [property: JsonPropertyName("color")] string Color,
    [property: JsonPropertyName("loggedIn")] bool LoggedIn,
    [property: JsonPropertyName("readyForVip")] bool ReadyForVip,
    [property: JsonPropertyName("cookieCount")] int CookieCount,
    [property: JsonPropertyName("qualities")] string[]? Qualities);

public sealed record StreamInfo(
    [property: JsonPropertyName("src")] string Src,
    [property: JsonPropertyName("quality")] string Quality,
    [property: JsonPropertyName("bitrate")] int Bitrate);

public sealed record LyricLine(
    [property: JsonPropertyName("time")] double Time,
    [property: JsonPropertyName("text")] string Text);

public sealed record Lyrics(
    [property: JsonPropertyName("lines")] LyricLine[]? Lines);

public sealed record QrPayload(
    [property: JsonPropertyName("session")] string Session,
    [property: JsonPropertyName("image")] string Image,
    [property: JsonPropertyName("status")] string Status,
    [property: JsonPropertyName("message")] string Message);

/// <summary>
/// Thin client for the local Python API. One shared <see cref="HttpClient"/>
/// keeps the loopback connection alive — the same 19x win measured on the
/// Python side applies here.
/// </summary>
public sealed class ApiClient : IDisposable
{
    private readonly HttpClient _http;
    private static readonly JsonSerializerOptions Options = new()
    {
        PropertyNameCaseInsensitive = true,
    };

    public string BaseUrl { get; }

    public ApiClient(string baseUrl)
    {
        BaseUrl = baseUrl.TrimEnd('/');
        _http = new HttpClient(new SocketsHttpHandler
        {
            PooledConnectionLifetime = TimeSpan.FromMinutes(5),
            ConnectTimeout = TimeSpan.FromSeconds(10),
        })
        {
            BaseAddress = new Uri(BaseUrl + "/"),
            Timeout = TimeSpan.FromSeconds(60),
        };
    }

    /// <summary>
    /// Build an absolute URI for a local API path.
    ///
    /// BaseUrl is stored without a trailing slash, so a path that does not
    /// start with '/' produced "http://127.0.0.1:8777api/..." — an invalid port
    /// that threw UriFormatException. Every caller that omitted the slash
    /// (logout, wipe) therefore failed silently while the UI reported success.
    /// </summary>
    public Uri Absolute(string path) =>
        new(BaseUrl + (path.Length > 0 && path[0] == '/' ? path : "/" + path));

    private async Task<T> GetAsync<T>(string path, CancellationToken ct)
    {
        using var resp = await _http.GetAsync(path, ct).ConfigureAwait(false);
        await EnsureOk(resp).ConfigureAwait(false);
        return (await resp.Content.ReadFromJsonAsync<T>(Options, ct).ConfigureAwait(false))!;
    }

    private async Task<T> PostAsync<T>(string path, object body, CancellationToken ct)
    {
        using var resp = await _http.PostAsJsonAsync(path, body, ct).ConfigureAwait(false);
        await EnsureOk(resp).ConfigureAwait(false);
        return (await resp.Content.ReadFromJsonAsync<T>(Options, ct).ConfigureAwait(false))!;
    }

    private static async Task EnsureOk(HttpResponseMessage resp)
    {
        if (resp.IsSuccessStatusCode) return;
        var text = await resp.Content.ReadAsStringAsync().ConfigureAwait(false);
        string detail = $"HTTP {(int)resp.StatusCode}";
        try
        {
            using var doc = JsonDocument.Parse(text);
            if (doc.RootElement.TryGetProperty("detail", out var d))
            {
                // FastAPI sends a plain string for raised HTTPExceptions but an
                // ARRAY for 422 validation errors. Calling GetString() on the
                // array threw InvalidOperationException, which is not an
                // ApiException — so callers' "catch (ApiException)" branches
                // skipped and the user saw a raw English exception instead of
                // the server's message.
                detail = d.ValueKind switch
                {
                    JsonValueKind.String => d.GetString() ?? detail,
                    JsonValueKind.Array => string.Join("；", d.EnumerateArray().Select(item =>
                        item.ValueKind == JsonValueKind.Object &&
                        item.TryGetProperty("msg", out var msg)
                            ? msg.GetString()
                            : item.ToString())),
                    JsonValueKind.Null => detail,
                    _ => d.ToString(),
                };
            }
        }
        catch (JsonException) { /* keep the status text */ }
        throw new ApiException(detail, (int)resp.StatusCode);
    }

    public Task<JsonElement> VerifyAccountAsync(string provider, CancellationToken ct) =>
        PostAsync<JsonElement>($"api/account/{Uri.EscapeDataString(provider)}/verify", new { }, ct);

    public Task<JsonElement> PlaybackSourceAsync(string provider, CancellationToken ct) =>
        GetAsync<JsonElement>($"api/playback-source/{Uri.EscapeDataString(provider)}", ct);

    public Task<JsonElement> SetPlaybackSourceAsync(string provider, string mode, string baseUrl, string path, CancellationToken ct) =>
        PostAsync<JsonElement>($"api/playback-source/{Uri.EscapeDataString(provider)}", new { mode, baseUrl, path }, ct);

    public Task<JsonElement> HealthAsync(CancellationToken ct) => GetAsync<JsonElement>("api/health", ct);

    /// <summary>Remove a bound account. Was previously hand-rolled with a raw
    /// HttpClient that ignored the status code, so failures reported success.</summary>
    public async Task LogoutAsync(string provider, CancellationToken ct)
    {
        using var resp = await _http
            .DeleteAsync($"api/account/{Uri.EscapeDataString(provider)}", ct)
            .ConfigureAwait(false);
        await EnsureOk(resp).ConfigureAwait(false);
    }

    /// <summary>Erase every local trace (accounts, playlists, history).</summary>
    public async Task<JsonElement> WipeAsync(CancellationToken ct)
    {
        using var resp = await _http
            .PostAsJsonAsync("api/privacy/wipe", new { confirm = true }, ct)
            .ConfigureAwait(false);
        await EnsureOk(resp).ConfigureAwait(false);
        return (await resp.Content.ReadFromJsonAsync<JsonElement>(Options, ct).ConfigureAwait(false));
    }

    public async Task<ProviderInfo[]> ProvidersAsync(CancellationToken ct)
    {
        var wrapper = await GetAsync<ProvidersResponse>("api/providers", ct).ConfigureAwait(false);
        return wrapper.Providers ?? Array.Empty<ProviderInfo>();
    }

    public async Task<(Track[] Tracks, JsonElement Errors)> SearchAsync(
        string query, string provider, int limit, CancellationToken ct)
    {
        var path = $"api/search?q={Uri.EscapeDataString(query)}&provider={Uri.EscapeDataString(provider)}&limit={limit}";
        var result = await GetAsync<SearchResponse>(path, ct).ConfigureAwait(false);
        return (result.Tracks ?? Array.Empty<Track>(), result.Errors);
    }

    public Task<StreamInfo> StreamAsync(string provider, string id, StreamRequest req, CancellationToken ct)
    {
        var query = new List<string> { $"quality={Uri.EscapeDataString(req.Quality)}" };
        if (req.Duration > 0) query.Add($"duration={req.Duration}");
        if (!string.IsNullOrEmpty(req.Hash320)) query.Add($"hash320={Uri.EscapeDataString(req.Hash320!)}");
        if (!string.IsNullOrEmpty(req.HashFlac)) query.Add($"hashFlac={Uri.EscapeDataString(req.HashFlac!)}");
        if (req.HasLossless) query.Add("hasLossless=true");
        if (!string.IsNullOrEmpty(req.Album)) query.Add($"album={Uri.EscapeDataString(req.Album)}");
        var path = $"api/stream/{Uri.EscapeDataString(provider)}/{Uri.EscapeDataString(id)}?{string.Join("&", query)}";
        return GetAsync<StreamInfo>(path, ct);
    }

    public Task<Lyrics> LyricsAsync(string provider, string id, CancellationToken ct) =>
        GetAsync<Lyrics>($"api/lyrics/{Uri.EscapeDataString(provider)}/{Uri.EscapeDataString(id)}", ct);

    public async Task QrCancelAsync(string provider, string session, CancellationToken ct)
    {
        using var response = await _http.DeleteAsync(
            $"api/login/{Uri.EscapeDataString(provider)}/qr?session={Uri.EscapeDataString(session)}", ct).ConfigureAwait(false);
        await EnsureOk(response).ConfigureAwait(false);
    }

    public Task<QrPayload> QrStartAsync(string provider, CancellationToken ct) =>
        PostAsync<QrPayload>($"api/login/{Uri.EscapeDataString(provider)}/qr", new { }, ct);

    public Task<QrPayload> QrPollAsync(string provider, string session, CancellationToken ct) =>
        GetAsync<QrPayload>($"api/login/{Uri.EscapeDataString(provider)}/poll?session={Uri.EscapeDataString(session)}", ct);

    public async Task<AccountResult> SaveCookieAsync(string provider, string cookie, CancellationToken ct)
    {
        var result = await PostAsync<AccountResult>($"api/account/{Uri.EscapeDataString(provider)}",
            new { cookie }, ct).ConfigureAwait(false);
        return result;
    }

    public async Task<Playlist[]> PlaylistsAsync(CancellationToken ct)
    {
        var wrapper = await GetAsync<PlaylistsResponse>("api/playlists", ct).ConfigureAwait(false);
        return wrapper.Playlists ?? Array.Empty<Playlist>();
    }

    public Task<PlaylistDetail> PlaylistAsync(int id, CancellationToken ct) =>
        GetAsync<PlaylistDetail>($"api/playlists/{id}", ct);

    public Task<AccountResult> AddToPlaylistAsync(int id, Track track, CancellationToken ct) =>
        PostAsync<AccountResult>($"api/playlists/{id}/items", ToPayload(track), ct);

    /// <summary>Shape the backend's TrackPayload expects.</summary>
    private static object ToPayload(Track track) => new
    {
        provider = track.Provider,
        id = track.Id,
        title = track.Title ?? "",
        artists = TrackShape.ArtistsOrSplit(track),
        album = track.Album ?? "",
        duration = track.Duration,
        cover = track.Cover ?? "",
        extra = track.Extra ?? new Dictionary<string, JsonElement>(),
    };

    public Task<AccountResult> CreatePlaylistAsync(string name, CancellationToken ct) =>
        PostAsync<AccountResult>("api/playlists", new { name }, ct);

    public Task<JsonElement> HistoryAsync(CancellationToken ct) => GetAsync<JsonElement>("api/history?limit=100", ct);

    public Task<JsonElement> AddHistoryAsync(Track track, CancellationToken ct) =>
        PostAsync<JsonElement>("api/history", ToPayload(track), ct);

    public void Dispose() => _http.Dispose();

    private sealed record ProvidersResponse(
        [property: JsonPropertyName("providers")] ProviderInfo[]? Providers);

    private sealed record SearchResponse(
        [property: JsonPropertyName("tracks")] Track[]? Tracks,
        [property: JsonPropertyName("errors")] JsonElement Errors);

    private sealed record PlaylistsResponse(
        [property: JsonPropertyName("playlists")] Playlist[]? Playlists);
}

public sealed record StreamRequest(string Quality, int Duration, string? Hash320, string? HashFlac, bool HasLossless, string? Album = null)
{
    public static StreamRequest FromTrack(Track track, string quality)
    {
        string? Read(string key) => track.Extra is not null && track.Extra.TryGetValue(key, out var value)
            ? value.ValueKind == JsonValueKind.String ? value.GetString() : value.ToString() : null;
        return new(quality, track.Duration, Read("hash320"), Read("hashFlac"), Read("hasLossless")?.ToLowerInvariant() == "true", Read("albumId"));
    }
}

public sealed record AccountResult(
    [property: JsonPropertyName("ok")] bool Ok,
    [property: JsonPropertyName("id")] int Id);

public sealed record Playlist(
    [property: JsonPropertyName("id")] int Id,
    [property: JsonPropertyName("name")] string Name,
    [property: JsonPropertyName("count")] int Count);

public sealed record PlaylistDetail(
    [property: JsonPropertyName("tracks")] Track[]? Tracks);

public sealed class ApiException : Exception
{
    public int StatusCode { get; }

    /// <summary>True when the server rejected the request's shape (422).</summary>
    public bool IsValidationError => StatusCode == 422;

    /// <summary>True when the provider refused to serve the track (424).</summary>
    public bool IsProviderRefusal => StatusCode == 424;

    public ApiException(string message, int statusCode = 0) : base(message)
    {
        StatusCode = statusCode;
    }
}
