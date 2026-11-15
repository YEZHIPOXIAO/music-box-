// Optional backend-start helper, not the application entry point.
// WinUI's generated Program.Main owns COM initialisation and Application.Start.
// The project uses the SDK's normal self-contained startup; do not introduce a
// second Program class or an independent bootstrap path here.
using System;

namespace MusicBox.Desktop;

public static class BackendStartup
{
    /// <summary>
    /// Start the Python backend before the window appears, and expose the
    /// failure instead of letting every API call time out later.
    /// </summary>
    public static bool StartBackend(out string message)
    {
        return BackendLauncher.Start(out message) != BackendState.Failed;
    }
}
