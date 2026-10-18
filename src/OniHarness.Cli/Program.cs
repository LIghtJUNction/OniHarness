using System.Diagnostics;
using System.Globalization;
using System.Reflection;
using System.Runtime.InteropServices;
using System.Text.Json;
using OniHarness;

return await Entry.RunAsync(args);

internal sealed record WorkerRequest(string Kind, string Path, string Name, string? GamePath, int Token = 0);
internal sealed record TestResult(string Name, string Evidence, string Status, string? Error = null,
    string? Fingerprint = null, int? StepsCompleted = null, double? InitialMass = null, double? FinalMass = null,
    string? Adapter = null, string? NativeSha256 = null, string? ManagedSha256 = null, double DurationMs = 0, int? ExitCode = null,
    string? StandardOutput = null, string? StandardError = null);
internal sealed record RunReport(int SchemaVersion, string HarnessVersion, DateTimeOffset StartedUtc, List<TestResult> Tests);

internal static class Entry
{
    public static async Task<int> RunAsync(string[] args)
    {
        try
        {
            if (args.Length > 0 && args[0] == "__worker") return await WorkerAsync(args[1], args[2]);
            if (args.Length == 0 || args[0] is "help" or "--help" or "-h") { Help(); return 0; }
            if (args[0] == "--version") { Console.WriteLine("OniHarness 0.1.0"); return 0; }
            var options = Options.Parse(args.Skip(1).ToArray());
            return args[0] switch
            {
                "doctor" => Doctor(options),
                "contract" => Contract(options),
                "test" => await TestAsync(options),
                _ => throw new ArgumentException($"Unknown command: {args[0]}")
            };
        }
        catch (Exception e)
        {
            Console.Error.WriteLine($"ERROR: {e.GetBaseException().Message}");
            return 2;
        }
    }

    private static int Doctor(Options options)
    {
        options.Allow("game-path");
        var installation = GameInstallation.Inspect(options.GamePath ?? throw new ArgumentException("Provide --game-path or ONIHARNESS_GAME_PATH."));
        Console.WriteLine(JsonSerializer.Serialize(installation, HarnessJson.Options));
        return installation.Adapter == "unsupported" ? 2 : 0;
    }

    private static int Contract(Options options)
    {
        options.Allow("type", "method", "parameters");
        Check.That(options.Paths.Count == 1, "contract requires one managed DLL path.");
        var contract = new MethodContract(options.Required("type"), options.Required("method"), options.Int("parameters", 0, 0, 100));
        bool found = AssemblyMetadata.HasMethod(options.Paths[0], contract);
        Console.WriteLine(JsonSerializer.Serialize(new { evidence = "metadata-only", found, contract }, HarnessJson.Options));
        return found ? 0 : 1;
    }

    private static async Task<int> TestAsync(Options options)
    {
        options.Allow("game-path", "assembly", "timeout", "repeat", "report", "filter");
        var requests = new List<WorkerRequest>();
        if (options.Values.TryGetValue("assembly", out string? assembly))
        {
            Check.That(options.Paths.Count == 0, "Use either scenario paths or --assembly.");
            assembly = Path.GetFullPath(assembly);
            foreach (var test in ManagedTests.Discover(assembly))
                requests.Add(new("managed", assembly, test.Name, options.GamePath, test.Token));
        }
        else
        {
            foreach (string path in options.Paths)
            {
                var files = Directory.Exists(path)
                    ? Directory.GetFiles(path, "*.json", SearchOption.AllDirectories).Order(StringComparer.Ordinal)
                    : new[] { path }.AsEnumerable();
                foreach (string file in files)
                {
                    var scenario = HarnessJson.Read<Scenario>(file);
                    scenario.Validate();
                    requests.Add(new("scenario", Path.GetFullPath(file), scenario.Name, options.GamePath));
                }
            }
        }
        if (options.Values.TryGetValue("filter", out string? filter))
            requests = requests.Where(r => r.Name.Contains(filter, StringComparison.OrdinalIgnoreCase)).ToList();
        Check.That(requests.Count > 0, "No matching tests found. Empty test runs are failures.");
        int repeat = options.Int("repeat", 1, 1, 100);
        int seconds = options.Int("timeout", 20, 1, 600);
        var report = new RunReport(1, "0.1.0", DateTimeOffset.UtcNow, []);
        using var cancellation = new CancellationTokenSource();
        ConsoleCancelEventHandler handler = (_, e) => { e.Cancel = true; cancellation.Cancel(); };
        Console.CancelKeyPress += handler;
        try
        {
            foreach (var request in requests)
            {
                string? baseline = null;
                for (int run = 0; run < repeat; run++)
                {
                    var result = await RunWorkerAsync(request, TimeSpan.FromSeconds(seconds), cancellation.Token);
                    if (result.Status == "passed" && result.Fingerprint != null)
                    {
                        if (baseline != null && baseline != result.Fingerprint)
                            result = result with { Status = "failed", Error = "Repeated native runs produced different state fingerprints." };
                        baseline ??= result.Fingerprint;
                    }
                    report.Tests.Add(result);
                    Console.WriteLine($"[{result.Status.ToUpperInvariant()}] {result.Name} ({result.Evidence}, run {run + 1}/{repeat}, {result.DurationMs:F1} ms)");
                    if (result.Error != null) Console.WriteLine("  " + result.Error);
                }
            }
        }
        finally { Console.CancelKeyPress -= handler; }
        if (options.Values.TryGetValue("report", out string? output))
        {
            string path = Path.GetFullPath(output);
            Directory.CreateDirectory(Path.GetDirectoryName(path)!);
            await File.WriteAllTextAsync(path, JsonSerializer.Serialize(report, HarnessJson.Options));
        }
        return report.Tests.All(r => r.Status == "passed") ? 0 : 1;
    }

    private static async Task<TestResult> RunWorkerAsync(WorkerRequest request, TimeSpan timeout, CancellationToken cancellation)
    {
        string directory = Directory.CreateTempSubdirectory("oniharness-").FullName;
        var watch = Stopwatch.StartNew();
        string evidence = request.Kind == "scenario" ? "native-sim" : "managed-host";
        try
        {
            string input = Path.Combine(directory, "request.json"), output = Path.Combine(directory, "result.json");
            await File.WriteAllTextAsync(input, JsonSerializer.Serialize(request, HarnessJson.Options), cancellation);
            var start = new ProcessStartInfo(Environment.ProcessPath ?? throw new InvalidOperationException("Cannot locate process host."));
            if (Path.GetFileNameWithoutExtension(start.FileName).Equals("dotnet", StringComparison.OrdinalIgnoreCase))
                start.ArgumentList.Add(Assembly.GetExecutingAssembly().Location);
            start.ArgumentList.Add("__worker"); start.ArgumentList.Add(input); start.ArgumentList.Add(output);
            start.Environment.Remove("DISPLAY"); start.Environment.Remove("WAYLAND_DISPLAY");
            var process = await ProcessRunner.RunAsync(start, timeout, cancellation);
            TestResult result;
            if (process.TimedOut) result = new(request.Name, evidence, "timeout", $"Worker exceeded {timeout.TotalSeconds} seconds.");
            else if (!File.Exists(output)) result = new(request.Name, evidence, "crashed", $"Worker exited without a result (exit {process.ExitCode}).");
            else result = HarnessJson.Read<TestResult>(output);
            if (process.ExitCode != 0 && result.Status == "passed") result = result with { Status = "crashed", Error = $"Worker exit code {process.ExitCode}." };
            return result with { DurationMs = watch.Elapsed.TotalMilliseconds, ExitCode = process.ExitCode,
                StandardOutput = process.StandardOutput, StandardError = process.StandardError };
        }
        finally { Directory.Delete(directory, recursive: true); }
    }

    private static async Task<int> WorkerAsync(string input, string output)
    {
        if (OperatingSystem.IsLinux()) NativeMethods.prctl(4, 0, 0, 0, 0);
        var request = HarnessJson.Read<WorkerRequest>(input);
        string evidence = request.Kind == "scenario" ? "native-sim" : "managed-host";
        TestResult result;
        try
        {
            if (request.Kind == "scenario")
            {
                var scenario = HarnessJson.Read<Scenario>(request.Path);
                Check.That(scenario.Assertions.Length > 0, "A CLI scenario must contain at least one assertion.");
                var game = GameInstallation.Inspect(request.GamePath ?? throw new InvalidOperationException("Native tests require --game-path or ONIHARNESS_GAME_PATH."));
                using var world = new NativeWorld(game, scenario);
                var initial = world.Snapshot();
                world.Step(scenario.Steps);
                var final = world.Snapshot();
                final.ValidateFinite();
                foreach (var assertion in scenario.Assertions) assertion.Verify(initial, final);
                result = new(request.Name, evidence, "passed", Fingerprint: final.Fingerprint(), StepsCompleted: world.StepsCompleted,
                    InitialMass: initial.TotalMass, FinalMass: final.TotalMass, Adapter: game.Adapter, NativeSha256: game.NativeSha256, ManagedSha256: game.ManagedSha256);
            }
            else
            {
                var context = new HarnessContext(request.GamePath);
                await ManagedTests.ExecuteAsync(request.Path, request.Token, context);
                var native = context.NativeInstallation;
                var managed = context.InvokedGameInstallation;
                evidence += native != null ? "+native-sim" : managed != null ? "+game-code" : "";
                result = new(request.Name, evidence, "passed", Adapter: native?.Adapter,
                    NativeSha256: native?.NativeSha256, ManagedSha256: (native ?? managed)?.ManagedSha256);
            }
        }
        catch (Exception e)
        {
            var error = e.GetBaseException();
            result = new(request.Name, evidence, error is NotSupportedException ? "unsupported" : error is TimeoutException ? "timeout" : "failed", error.Message);
        }
        await File.WriteAllTextAsync(output, JsonSerializer.Serialize(result, HarnessJson.Options));
        return result.Status == "passed" ? 0 : 1;
    }

    private static void Help() => Console.WriteLine("""
        OniHarness 0.1.0 — headless testing for Oxygen Not Included mods
        oniharness doctor --game-path PATH
        oniharness test SCENARIO.json|DIRECTORY [--game-path PATH] [--repeat N] [--timeout SECONDS] [--report FILE]
        oniharness test --assembly TESTS.dll [--game-path PATH] [--filter TEXT] [--timeout SECONDS] [--report FILE]
        oniharness contract GAME.dll --type TYPE --method METHOD --parameters COUNT
        ONIHARNESS_GAME_PATH supplies the installation path. No ONI or Unity Player is launched.
        Exit codes: 0 success, 1 test failure/unsupported/timeout/crash, 2 usage/configuration error.
        """);

    private sealed class Options
    {
        public Dictionary<string, string> Values { get; } = new(StringComparer.Ordinal);
        public List<string> Paths { get; } = [];
        public string? GamePath => Values.GetValueOrDefault("game-path") ?? Environment.GetEnvironmentVariable("ONIHARNESS_GAME_PATH");
        public string Required(string key) => Values.TryGetValue(key, out string? value) ? value : throw new ArgumentException($"Missing --{key}.");
        public int Int(string key, int fallback, int min, int max)
        {
            if (!Values.TryGetValue(key, out string? raw)) return fallback;
            Check.That(int.TryParse(raw, NumberStyles.None, CultureInfo.InvariantCulture, out int value) && value >= min && value <= max, $"--{key} must be {min}..{max}.");
            return value;
        }
        public void Allow(params string[] keys)
        {
            foreach (string key in Values.Keys) Check.That(keys.Contains(key), $"Unknown option: --{key}");
            if (!keys.Contains("assembly") && !keys.Contains("parameters")) Check.That(Paths.Count == 0, "Unexpected positional argument.");
        }
        public static Options Parse(string[] args)
        {
            var options = new Options();
            for (int i = 0; i < args.Length; i++)
            {
                if (!args[i].StartsWith("--", StringComparison.Ordinal)) { options.Paths.Add(args[i]); continue; }
                string key = args[i][2..];
                Check.That(i + 1 < args.Length && !args[i + 1].StartsWith("--", StringComparison.Ordinal), $"Missing value for --{key}.");
                Check.That(options.Values.TryAdd(key, args[++i]), $"Duplicate option: --{key}");
            }
            return options;
        }
    }

    private static class NativeMethods
    {
        [DllImport("libc", SetLastError = true)]
        internal static extern int prctl(int option, ulong arg2, ulong arg3, ulong arg4, ulong arg5);
    }
}
