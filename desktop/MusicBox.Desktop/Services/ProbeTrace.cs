using System;
using System.IO;
namespace MusicBox.Desktop.Services;
internal static class ProbeTrace
{
    internal static void Write(string stage)
    {
        var root = Environment.GetEnvironmentVariable("MUSICBOX_OFFICIAL_LOGIN_PROBE");
        if (string.IsNullOrWhiteSpace(root)) return;
        try { Directory.CreateDirectory(root); File.AppendAllText(Path.Combine(root,"stages.log"),DateTime.UtcNow.ToString("O")+" "+stage+Environment.NewLine); }
        catch (Exception) { }
    }
}
