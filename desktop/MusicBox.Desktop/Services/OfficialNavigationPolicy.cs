using System;
namespace MusicBox.Desktop.Services;

/// <summary>Upgrade only independently verified official Kuwo OAuth HTTP hops.
/// Never allow an authorization code or login cookie to traverse plaintext.</summary>
public static class OfficialNavigationPolicy
{
    public static bool IsHttps(string value) => Uri.TryCreate(value, UriKind.Absolute, out var uri)
        && uri.Scheme == "https" && string.IsNullOrEmpty(uri.UserInfo);

    public static Uri? SecureCallback(string provider, string value)
    {
        if (provider != "kuwo" || !Uri.TryCreate(value, UriKind.Absolute, out var uri)
            || uri.Scheme != "http" || uri.Host != "i.kuwo.cn" || !uri.IsDefaultPort
            || !string.IsNullOrEmpty(uri.UserInfo) || !string.IsNullOrEmpty(uri.Fragment)) return null;
        if (uri.AbsolutePath is not ("/US/platform/WeixinCallback.jsp" or "/US/platform/qq.jsp"
            or "/US_NEW/kuwo/login_by_weixin_web" or "/US_NEW/kuwo/login_by_qq_web")) return null;
        foreach (var pair in uri.Query.TrimStart('?').Split('&', StringSplitOptions.RemoveEmptyEntries))
        {
            var pieces = pair.Split('=', 2);
            if (Uri.UnescapeDataString(pieces[0]) != "callbackUrl") continue;
            if (pieces.Length != 2 || !Uri.TryCreate(Uri.UnescapeDataString(pieces[1]), UriKind.Absolute, out var callback)
                || callback.Scheme != "https" || callback.Host != "www.kuwo.cn" || !callback.IsDefaultPort
                || callback.AbsolutePath != "/callback" || !string.IsNullOrEmpty(callback.UserInfo)) return null;
        }
        return new UriBuilder(uri) { Scheme = "https", Port = -1 }.Uri;
    }
}
