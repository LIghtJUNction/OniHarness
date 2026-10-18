using OniHarness;
using System.Runtime.CompilerServices;

public static class Fixtures
{
    [ModuleInitializer]
    public static void Initialize()
    {
        if (Environment.GetEnvironmentVariable("ONIHARNESS_TEST_SENTINEL") is string path)
            File.WriteAllText(path, "executed");
    }

    [HarnessTest] public static void Success() => Check.That(true, "Unexpected failure.");
    [HarnessTest] public static void Failure() => Check.That(false, "Intentional assertion failure.");
    [HarnessTest] public static void Hang()
    {
        if (Environment.GetEnvironmentVariable("ONIHARNESS_TEST_WORKER_PID") is string path)
            File.WriteAllText(path, Environment.ProcessId.ToString());
        Thread.Sleep(Timeout.Infinite);
    }
    [HarnessTest] public static void Crash() => Environment.Exit(23);
    [HarnessTest] public static async Task AsyncFailure() { await Task.Yield(); throw new InvalidOperationException("Async assertion failure."); }
    [HarnessTest] public static void FloodOutput() { Console.Write(new string('x', 100000)); Console.Error.Write(new string('y', 100000)); }
}
