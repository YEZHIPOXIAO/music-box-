using System;
using System.Diagnostics;
using System.IO;
using System.Net.Http;
using System.Text;
using System.Threading;

namespace MusicBox.Desktop;

public enum BackendState
{
    Started,
    AlreadyRunning,
    Failed,
}

/// <summary>
/// Starts the bundled Python backend and waits until it answers /api/health.
///
/// The backend owns all provider logic, cookie encryption and the audio proxy;
/// this shell is only a UI. Three outcomes are distinguished because the old
/// version returned a *dead* process as success, leaving the window alive with
/// every request failing to connect.
/// </summary>
public static class BackendLauncher
{
    private static readonly HttpClient Probe = new() { Timeout = TimeSpan.FromSeconds(3) };
    private static readonly StringBuilder Stderr = new();

    public static BackendState Start(out string message, string port = "8777")
    {
        message = "";
        var baseUrl = $"http://127.0.0.1:{port}";

        if (IsHealthy(baseUrl))
        {
            message = "已连接到正在运行的后端";
            return BackendState.AlreadyRunning;
        }

        var exe = ResolvePython();
        if (exe is null)
        {
            message = "未找到 Python 运行时。请安装依赖后重试，或设置 MUSICBOX_PYTHON 环境变量。";
            return BackendState.Failed;
        }

        var workDir = ResolveBackendDir();
        if (workDir is null)
        {
            message = "未找到后端目录（包含 musicbox/ 的文件夹）。";
            return BackendState.Failed;
        }

        var info = new ProcessStartInfo
        {
            FileName = exe,
            Arguments = $"-m musicbox --ui browser --no-browser --port {port}",
            WorkingDirectory = workDir,
            UseShellExecute = false,
            CreateNoWindow = true,
            RedirectStandardOutput = true,
            RedirectStandardError = true,
            StandardOutputEncoding = Encoding.UTF8,
            StandardErrorEncoding = Encoding.UTF8,
        };
        info.Environment["MUSICBOX_LOG"] = "warning";
        // Without these, Python prints the banner in the locale codec and can
        // die with UnicodeEncodeError when stdout is a redirected pipe.
        info.Environment["PYTHONUTF8"] = "1";
        info.Environment["PYTHONIOENCODING"] = "utf-8";

        Process proc;
        try
        {
            proc = Process.Start(info) ?? throw new InvalidOperationException("Process.Start 返回 null");
        }
        catch (Exception ex)
        {
            message = $"无法启动后端进程：{ex.Message}";
            return BackendState.Failed;
        }

        lock (Stderr) Stderr.Clear();
        proc.OutputDataReceived += (_, _) => { };
        proc.ErrorDataReceived += (_, e) =>
        {
            if (!string.IsNullOrEmpty(e.Data))
            {
                lock (Stderr) Stderr.AppendLine(e.Data);
            }
        };
        proc.BeginOutputReadLine();
        proc.BeginErrorReadLine();

        for (var i = 0; i < 100; i++)
        {
            if (proc.HasExited)
            {
                message = $"后端启动后立即退出（代码 {proc.ExitCode}）：\n{TailStderr()}";
                return BackendState.Failed;
            }
            if (IsHealthy(baseUrl))
            {
                App.Backend = proc;
                message = "后端已启动";
                return BackendState.Started;
            }
            Thread.Sleep(150);
        }

        // Never hand a possibly-broken process to the app: kill it and say why.
        try { proc.Kill(entireProcessTree: true); } catch (Exception) { /* best effort */ }
        message = $"后端在 15 秒内未响应：\n{TailStderr()}";
        return BackendState.Failed;
    }

    private static string TailStderr()
    {
        lock (Stderr)
        {
            var text = Stderr.ToString().Trim();
            if (text.Length == 0) return "（后端没有输出任何错误信息）";
            return text.Length > 1200 ? text[^1200..] : text;
        }
    }

    private static bool IsHealthy(string baseUrl)
    {
        try
        {
            using var resp = Probe.GetAsync($"{baseUrl}/api/health").GetAwaiter().GetResult();
            return resp.IsSuccessStatusCode;
        }
        catch (Exception)
        {
            return false;
        }
    }

    /// <summary>Find the Python interpreter that has the backend installed.</summary>
    private static string? ResolvePython()
    {
        var env = Environment.GetEnvironmentVariable("MUSICBOX_PYTHON");
        if (!string.IsNullOrEmpty(env) && File.Exists(env)) return env;

        var bundled = Path.Combine(AppContext.BaseDirectory, "runtime", "python.exe");
        if (File.Exists(bundled)) return bundled;
        var repo = ResolveRepoRoot();
        if (repo is not null)
        {
            // Development fallback; installed packages use runtime/python.exe.
            foreach (var candidate in new[]
                     {
                         Path.Combine(repo, ".venv", "Scripts", "python.exe"),
                         Path.Combine(repo, "venv", "Scripts", "python.exe"),
                     })
            {
                if (File.Exists(candidate)) return candidate;
            }
        }
        return "python";
    }

    private static string? ResolveBackendDir() => ResolveRepoRoot();

    /// <summary>Walk up from the exe to the folder that contains musicbox/.</summary>
    private static string? ResolveRepoRoot()
    {
        var dir = new DirectoryInfo(AppContext.BaseDirectory);
        for (var i = 0; i < 8 && dir is not null; i++, dir = dir.Parent)
        {
            if (Directory.Exists(Path.Combine(dir.FullName, "musicbox")))
                return dir.FullName;
            // Also accept a bundled runtime/ layout for a future installer.
            if (Directory.Exists(Path.Combine(dir.FullName, "runtime", "musicbox")))
                return Path.Combine(dir.FullName, "runtime");
        }
        return null;
    }
}
