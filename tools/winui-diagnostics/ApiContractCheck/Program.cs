using System;
using System.Net;
using System.Net.Http;
using System.Reflection;
using System.Threading.Tasks;
using MusicBox.Desktop.Services;

using var api = new ApiClient("http://127.0.0.1:8777/");
foreach (var path in new[] { "api/privacy/wipe", "/api/privacy/wipe" })
{
    var actual = api.Absolute(path).AbsoluteUri;
    if (actual != "http://127.0.0.1:8777/api/privacy/wipe")
        throw new Exception("Absolute URI regression: " + actual);
}
Console.WriteLine("PASS Absolute() accepts paths with/without leading slash");

var method = typeof(ApiClient).GetMethod("EnsureOk", BindingFlags.NonPublic | BindingFlags.Static)
    ?? throw new Exception("EnsureOk method not found");
foreach (var sample in new[]
{
    (Code: 422, Body: "{\"detail\":[{\"msg\":\"query too long\"},{\"msg\":\"required field\"}]}", Expected: "query too long；required field"),
    (Code: 424, Body: "{\"detail\":\"provider refused\"}", Expected: "provider refused"),
    (Code: 502, Body: "<html>upstream failure</html>", Expected: "HTTP 502")
})
{
    using var response = new HttpResponseMessage((HttpStatusCode)sample.Code)
    {
        Content = new StringContent(sample.Body)
    };
    try
    {
        await (Task)method.Invoke(null, new object[] { response })!;
        throw new Exception("Expected ApiException");
    }
    catch (ApiException e)
    {
        if (e.StatusCode != sample.Code || e.Message != sample.Expected)
            throw new Exception($"Wrong API error: {e.StatusCode} {e.Message}");
    }
}
Console.WriteLine("PASS EnsureOk() handles array/string/non-JSON errors as ApiException with status");

var scratch = System.IO.Path.Combine(System.IO.Directory.GetCurrentDirectory(), "_scratch", "MusicBox-path-check-" + Guid.NewGuid().ToString("N"));
System.IO.Directory.CreateDirectory(scratch);
try
{
    var denied = System.IO.Path.Combine(scratch, "not-a-directory");
    System.IO.File.WriteAllText(denied, "blocks LocalAppData directory creation");
    var portable = System.IO.Path.Combine(scratch, "portable");
    System.IO.Directory.CreateDirectory(portable);
    System.IO.File.WriteAllText(System.IO.Path.Combine(portable, "portable-mode"), "");
    var root = AppDataPaths.Resolve(portable, null, denied);
    if (root != System.IO.Path.Combine(portable, "data")) throw new Exception("Portable mode tried LocalAppData");
    foreach (var platform in new[] { "qq", "kugou", "kuwo" })
    {
        var profile = AppDataPaths.LoginProfile(root, platform);
        if (!System.IO.Directory.Exists(profile)) throw new Exception("Profile not created");
    }
    var explicitRoot = System.IO.Path.Combine(scratch, "explicit");
    if (AppDataPaths.Resolve(portable, explicitRoot, denied) != explicitRoot) throw new Exception("Override ignored");
    Console.WriteLine("PASS portable mode and MUSICBOX_HOME isolate all three profiles from unwritable LocalAppData");
}
finally { System.IO.Directory.Delete(scratch, true); }

foreach (var path in new[] { "/US/platform/WeixinCallback.jsp", "/US/platform/qq.jsp", "/US_NEW/kuwo/login_by_weixin_web", "/US_NEW/kuwo/login_by_qq_web" })
{
    var original = "http://i.kuwo.cn" + path + "?code=SYNTHETIC&callbackUrl=https%3A%2F%2Fwww.kuwo.cn%2Fcallback";
    var secure = OfficialNavigationPolicy.SecureCallback("kuwo", original);
    if (secure is null || secure.Scheme != "https" || secure.Query != new Uri(original).Query) throw new Exception("Official callback upgrade broken");
}
foreach (var bad in new[] {
    "http://evil.example/US/platform/qq.jsp", "http://i.kuwo.cn.evil.example/US/platform/qq.jsp",
    "http://i.kuwo.cn/other", "http://user@i.kuwo.cn/US/platform/qq.jsp", "http://i.kuwo.cn:123/US/platform/qq.jsp",
    "http://i.kuwo.cn/US/platform/qq.jsp?callbackUrl=https%3A%2F%2Fevil.example%2Fcallback" })
    if (OfficialNavigationPolicy.SecureCallback("kuwo", bad) is not null) throw new Exception("Unexpected callback trusted");
if (OfficialNavigationPolicy.SecureCallback("qq", "http://i.kuwo.cn/US/platform/qq.jsp") is not null) throw new Exception("Cross-provider callback allowed");
Console.WriteLine("PASS exact Kuwo OAuth hops upgrade to TLS and untrusted hosts/paths remain blocked");
var extras = new System.Collections.Generic.Dictionary<string, System.Text.Json.JsonElement>
{
    ["albumId"] = System.Text.Json.JsonSerializer.SerializeToElement("ALBUM42"),
    ["hash320"] = System.Text.Json.JsonSerializer.SerializeToElement("HQ"),
    ["hashFlac"] = System.Text.Json.JsonSerializer.SerializeToElement("SQ"),
};
var request = StreamRequest.FromTrack(new Track("kugou", "hash", "title", "artist", "", 326, "", extras), "320");
if (request.Album != "ALBUM42" || request.Hash320 != "HQ" || request.HashFlac != "SQ" || request.Duration != 326) throw new Exception("Playback metadata dropped");
Console.WriteLine("PASS search and playlist playback preserve albumId, file hashes and duration");
var first = new Track("kugou", "A", "A", "artist", "", 1, "", null);
var second = first with { Id = "B", Title = "B" };
var third = first with { Id = "C", Title = "C" };
var queue = new PlaybackQueue(_ => 0);
queue.Replace(new[] { first, second, third }, second);
if (queue.Next()?.Id != "C" || queue.Next(true) is not null) throw new Exception("Sequential boundary broken");
queue.Mode = PlaybackMode.RepeatAll;
if (queue.Next()?.Id != "A" || queue.Previous()?.Id != "C") throw new Exception("List loop broken");
queue.Mode = PlaybackMode.RepeatOne;
if (queue.Next(true)?.Id != "C") throw new Exception("Single loop failed on natural end");
queue.Select(1); queue.Mode = PlaybackMode.Shuffle;
if (queue.Next()?.Id == "B" || queue.Previous()?.Id != "B") throw new Exception("Shuffle history broken");
queue.Remove(0);
if (queue.Current?.Id != "B" || queue.Index != 0) throw new Exception("Remove shifted current track");
queue.KeepCurrent();
if (queue.Items.Count != 1 || queue.Current?.Id != "B") throw new Exception("Keep current broken");
Console.WriteLine("PASS sequential/list/single/shuffle navigation, previous history and queue edits");
