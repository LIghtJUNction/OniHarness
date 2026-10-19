using System.Diagnostics;
using System.Text.Json;
using OniHarness;

string root = Directory.GetCurrentDirectory();
string cli = Path.Combine(root, "src/OniHarness.Cli/bin/Release/net10.0/OniHarness.Cli.dll");
string fixture = Path.Combine(root, "tests/RunnerFixtures/bin/Release/net10.0/RunnerFixtures.dll");
string scratch = Directory.CreateTempSubdirectory("oniharness-tests-").FullName;
int passed = 0;
try
{
    await Test("runtime hooks select the exact nongeneric overload", () =>
    {
        var method = OniHarness.Runtime.RuntimeMethods.RequireNonGeneric(typeof(UiFactoryFixture), "Create", typeof(object), typeof(bool));
        Check.That((string)method.Invoke(null, [new object(), true])! == "native-ui", "Selected a generic or unrelated overload.");
        try
        {
            OniHarness.Runtime.RuntimeMethods.RequireNonGeneric(typeof(UiFactoryFixture), "Create", typeof(int));
            throw new InvalidOperationException("Missing signature was accepted.");
        }
        catch (MissingMethodException) { }
        return Task.CompletedTask;
    });
    await Test("invalid scenarios are rejected before native execution", () =>
    {
        Reject(() => new Scenario { Width = -1 }.Validate());
        Reject(() => new Scenario { SecondsPerStep = float.NaN }.Validate());
        Reject(() => new Scenario { Fill = new() { Mass = -1 } }.Validate());
        Reject(() => new Scenario { Regions = [new() { X = 15, Width = 100 }] }.Validate());
        Reject(() => new Scenario { Assertions = [new() { Kind = "typo" }] }.Validate());
        Reject(() => JsonSerializer.Deserialize<Scenario>("{\"stpes\":5}", HarnessJson.Options));
        new Scenario().Validate();
        return Task.CompletedTask;
    });
    await Test("assertions detect changed physical outcomes", () =>
    {
        var initial = new Snapshot(1, 1, [0], [1], [300]);
        var final = new Snapshot(1, 1, [0], [2], [400]);
        Reject(() => new Expectation { Kind = "massConserved" }.Verify(initial, final));
        Reject(() => new Expectation { Kind = "cellTemperature", Max = 350 }.Verify(initial, final));
        Reject(() => new Snapshot(1, 1, [0], [float.NaN], [300]).ValidateFinite());
        Check.That(initial.Fingerprint() != final.Fingerprint(), "Changed states have the same fingerprint.");
        return Task.CompletedTask;
    });
    await Test("metadata discovery never executes module initializers", () =>
    {
        string sentinel = Path.Combine(scratch, "sentinel");
        Environment.SetEnvironmentVariable("ONIHARNESS_TEST_SENTINEL", sentinel);
        var tests = ManagedTests.Discover(fixture);
        Check.That(tests.Length == 6 && !File.Exists(sentinel), "Discovery executed the test assembly or missed tests.");
        Check.That(AssemblyMetadata.HasMethod(fixture, new("Fixtures", "Success", 0)), "Existing method was missed.");
        Check.That(!AssemblyMetadata.HasMethod(fixture, new("Fixtures", "Missing", 0)), "Missing method was reported present.");
        return Task.CompletedTask;
    });
    await Test("successful managed test executes in the worker", async () =>
    {
        var result = await Cli("test", "--assembly", fixture, "--filter", "Success", "--report", Path.Combine(scratch, "success.json"));
        Check.That(result.ExitCode == 0 && result.StandardOutput.Contains("[PASSED]"), result.StandardOutput + result.StandardError);
        Check.That(File.Exists(Path.Combine(scratch, "sentinel")), "Worker did not execute the module initializer.");
    });
    await Test("assertion and async failures fail the CLI", async () =>
    {
        var result = await Cli("test", "--assembly", fixture, "--filter", "Failure");
        Check.That(result.ExitCode == 1 && result.StandardOutput.Split("[FAILED]").Length == 3, result.StandardOutput);
    });
    await Test("timeout kills and reaps the worker", async () =>
    {
        string pidPath = Path.Combine(scratch, "worker.pid");
        Environment.SetEnvironmentVariable("ONIHARNESS_TEST_WORKER_PID", pidPath);
        var watch = Stopwatch.StartNew();
        var result = await Cli("test", "--assembly", fixture, "--filter", "Hang", "--timeout", "1");
        Check.That(result.ExitCode == 1 && result.StandardOutput.Contains("[TIMEOUT]") && watch.Elapsed.TotalSeconds < 8, result.StandardOutput);
        Check.That(File.Exists(pidPath), "Worker did not start before timeout.");
        int pid = int.Parse(File.ReadAllText(pidPath));
        try
        {
            using var worker = Process.GetProcessById(pid);
            Check.That(worker.HasExited, "Timed-out worker is still alive.");
        }
        catch (ArgumentException) { }
        Environment.SetEnvironmentVariable("ONIHARNESS_TEST_WORKER_PID", null);
    });
    await Test("abrupt worker exit is a crash, not a pass", async () =>
    {
        var result = await Cli("test", "--assembly", fixture, "--filter", "Crash");
        Check.That(result.ExitCode == 1 && result.StandardOutput.Contains("[CRASHED]"), result.StandardOutput);
    });
    await Test("large stdout and stderr are drained with bounded reports", async () =>
    {
        string report = Path.Combine(scratch, "flood.json");
        var result = await Cli("test", "--assembly", fixture, "--filter", "FloodOutput", "--report", report);
        Check.That(result.ExitCode == 0 && new FileInfo(report).Length < 40000 && File.ReadAllText(report).Contains("output truncated"), result.StandardOutput);
    });
    await Test("empty test selections and unknown options fail", async () =>
    {
        Check.That((await Cli("test", "--assembly", fixture, "--filter", "does-not-exist")).ExitCode == 2, "Empty selection passed.");
        Check.That((await Cli("test", "--assembly", fixture, "--timout", "1")).ExitCode == 2, "Unknown option passed.");
    });
    await Test("unknown game binaries are reported unsupported without loading", async () =>
    {
        string game = Path.Combine(scratch, "game");
        Directory.CreateDirectory(Path.Combine(game, "OxygenNotIncluded_Data", "Plugins"));
        Directory.CreateDirectory(Path.Combine(game, "OxygenNotIncluded_Data", "Managed"));
        File.WriteAllText(Path.Combine(game, "OxygenNotIncluded_Data", "Plugins", "libSimDLL.so"), "not a native library");
        File.WriteAllText(Path.Combine(game, "OxygenNotIncluded_Data", "Managed", "Assembly-CSharp.dll"), "not a managed assembly");
        var result = await Cli("doctor", "--game-path", game);
        Check.That(result.ExitCode == 2 && result.StandardOutput.Contains("unsupported"), result.StandardOutput);
        Reject(() => new NativeWorld(GameInstallation.Inspect(game), new Scenario()));
    });
    Console.WriteLine($"PASS: {passed} game-free behavioral checks.");
    return 0;
}
catch (Exception e) { Console.Error.WriteLine(e); return 1; }
finally
{
    Environment.SetEnvironmentVariable("ONIHARNESS_TEST_SENTINEL", null);
    Environment.SetEnvironmentVariable("ONIHARNESS_TEST_WORKER_PID", null);
    Directory.Delete(scratch, true);
}

async Task Test(string name, Func<Task> body) { await body(); passed++; Console.WriteLine("PASS: " + name); }
void Reject(Action action)
{
    try { action(); } catch (Exception e) when (e is InvalidDataException or JsonException or NotSupportedException) { return; }
    throw new InvalidOperationException("Expected rejection.");
}
Task<ProcessOutcome> Cli(params string[] arguments)
{
    var start = new ProcessStartInfo("dotnet");
    start.ArgumentList.Add(cli);
    foreach (string argument in arguments) start.ArgumentList.Add(argument);
    return ProcessRunner.RunAsync(start, TimeSpan.FromSeconds(10));
}

static class UiFactoryFixture
{
    public static string Create(object original, bool active) => "native-ui";
    public static T Create<T>(object original, bool active) => default!;
    public static string Create(string original, bool active) => "other-overload";
}
