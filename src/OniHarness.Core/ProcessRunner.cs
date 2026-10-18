using System.Diagnostics;
using System.Text;

namespace OniHarness;

public sealed record ProcessOutcome(int? ExitCode, bool TimedOut, string StandardOutput, string StandardError);

public static class ProcessRunner
{
    /// <summary>Bounds output memory, drains both pipes and kills the whole worker tree on timeout/cancellation.</summary>
    public static async Task<ProcessOutcome> RunAsync(ProcessStartInfo start, TimeSpan timeout, CancellationToken cancellation = default)
    {
        Check.That(timeout > TimeSpan.Zero && timeout <= TimeSpan.FromMinutes(10), "Process timeout must be > 0 and <= 10 minutes.");
        start.UseShellExecute = false;
        start.RedirectStandardOutput = true;
        start.RedirectStandardError = true;
        using var process = new Process { StartInfo = start };
        process.Start();
        Task<string> stdout = ReadBoundedAsync(process.StandardOutput);
        Task<string> stderr = ReadBoundedAsync(process.StandardError);
        using var deadline = CancellationTokenSource.CreateLinkedTokenSource(cancellation);
        deadline.CancelAfter(timeout);
        bool timedOut = false;
        try { await process.WaitForExitAsync(deadline.Token); }
        catch (OperationCanceledException)
        {
            timedOut = !cancellation.IsCancellationRequested;
            try { process.Kill(entireProcessTree: true); } catch (InvalidOperationException) { }
            await process.WaitForExitAsync(CancellationToken.None);
        }
        var outcome = new ProcessOutcome(process.ExitCode, timedOut, await stdout, await stderr);
        cancellation.ThrowIfCancellationRequested();
        return outcome;
    }

    private static async Task<string> ReadBoundedAsync(StreamReader reader)
    {
        const int limit = 16384;
        var result = new StringBuilder();
        var buffer = new char[4096];
        int count;
        bool truncated = false;
        while ((count = await reader.ReadAsync(buffer)) > 0)
        {
            int take = Math.Min(count, limit - result.Length);
            if (take > 0) result.Append(buffer, 0, take);
            if (take < count) truncated = true;
        }
        if (truncated) result.Append("\n[output truncated]");
        return result.ToString();
    }
}
