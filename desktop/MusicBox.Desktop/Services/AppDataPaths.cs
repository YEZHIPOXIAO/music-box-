using System;
using System.IO;

namespace MusicBox.Desktop.Services;

/// <summary>One writable data root shared by WinUI, WebView2 and Python.
/// Portable packages opt in with a marker file; installed apps use LocalAppData.
/// Never modify Windows ACLs or silently move an existing account database.</summary>
public static class AppDataPaths
{
    public static string Resolve(string baseDirectory, string? overridePath, string localAppData)
    {
        var root = !string.IsNullOrWhiteSpace(overridePath)
            ? Path.GetFullPath(overridePath)
            : File.Exists(Path.Combine(baseDirectory, "portable-mode"))
                ? Path.Combine(baseDirectory, "data")
                : Path.Combine(localAppData, "MusicBox");
        VerifyWritable(root);
        return root;
    }

    public static void VerifyWritable(string root)
    {
        Directory.CreateDirectory(root);
        // Directory.CreateDirectory succeeds even when an existing directory
        // denies writes. Probe an actual file before starting the backend.
        var probe = Path.Combine(root, ".write-probe-" + Guid.NewGuid().ToString("N"));
        using var stream = new FileStream(probe, FileMode.CreateNew, FileAccess.ReadWrite,
            FileShare.None, 1, FileOptions.DeleteOnClose);
        stream.WriteByte(1);
    }

    public static string LoginProfile(string root, string provider)
    {
        if (provider is not ("qq" or "kugou" or "kuwo"))
            throw new ArgumentException("Unknown login provider", nameof(provider));
        var profile = Path.Combine(root, "WebViewLogin", provider);
        VerifyWritable(profile);
        return profile;
    }
}
