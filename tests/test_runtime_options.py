"""Execute production runtime options against game-free API doubles.

Each case starts a separate .NET process with real command-line arguments.
This verifies companion policy and rendering state changes, not Unity/ONI UI.
"""
import html
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
RUNTIME = ROOT / "runtime/OniHarness.Runtime"

DOUBLES = r'''
using System;
using System.Collections.Generic;
using System.IO;
using System.Reflection;
using System.Text.Json;

namespace HarmonyLib {
    public sealed class Harmony {
        public static int ManualPatches;
        public void Patch(MethodInfo original, HarmonyMethod prefix) {
            if (original == null || prefix == null) throw new Exception("missing patch target");
            ManualPatches++;
        }
    }
    public sealed class HarmonyMethod { public HarmonyMethod(Type type, string name) {} }
    public static class AccessTools {
        public static MethodInfo Method(Type type, string name) => type.GetMethod(name, BindingFlags.Public | BindingFlags.NonPublic | BindingFlags.Static | BindingFlags.Instance);
    }
}
namespace KMod {
    public class UserMod2 {
        public static int BaseLoads;
        public virtual void OnLoad(HarmonyLib.Harmony harmony) { BaseLoads++; }
    }
}
namespace Newtonsoft.Json {
    public static class JsonConvert {
        private static readonly JsonSerializerOptions options = new JsonSerializerOptions { PropertyNameCaseInsensitive = true };
        public static T DeserializeObject<T>(string text) => JsonSerializer.Deserialize<T>(text, options);
        public static string SerializeObject(object value) => JsonSerializer.Serialize(value, options);
    }
}
namespace PeterHan.PLib.Core {
    public static class PUtil {
        public static int Initializations;
        public static void InitLibrary() { Initializations++; }
    }
}
namespace PeterHan.PLib.Options {
    [AttributeUsage(AttributeTargets.Class)] public sealed class ConfigFileAttribute : Attribute { public ConfigFileAttribute(string path, bool global) {} }
    [AttributeUsage(AttributeTargets.Property)] public sealed class OptionAttribute : Attribute { public OptionAttribute(string title, string description) {} }
    [AttributeUsage(AttributeTargets.Property)] public sealed class LimitAttribute : Attribute { public LimitAttribute(double min, double max) {} }
    [AttributeUsage(AttributeTargets.Property)] public sealed class RestartRequiredAttribute : Attribute {}
    public sealed class POptions {
        public static int Registrations;
        public static Type RegisteredType;
        public void RegisterOptions(KMod.UserMod2 owner, Type type) { Registrations++; RegisteredType = type; }
        public static T ReadSettings<T>() {
            string text = Environment.GetEnvironmentVariable("ONIHARNESS_TEST_OPTIONS");
            return text == null ? default(T) : JsonSerializer.Deserialize<T>(text);
        }
    }
}
namespace UnityEngine.Rendering {
    public enum GraphicsDeviceType { Null, OpenGLCore }
    public static class OnDemandRendering { public static int renderFrameInterval = 7; }
}
namespace UnityEngine {
    public class Object {
        public static int PersistentObjects;
        public static void DontDestroyOnLoad(object value) { PersistentObjects++; }
    }
    public class MonoBehaviour : Object {}
    public class GameObject : Object {
        public static int Creations;
        public static object LastComponent;
        public GameObject(string name) { Creations++; }
        public T AddComponent<T>() where T : new() { var item = new T(); LastComponent = item; return item; }
    }
    public static class Application {
        public static bool runInBackground;
        public static bool isBatchMode;
        public static int targetFrameRate = 144;
        public static string unityVersion = "fixture";
    }
    public static class SystemInfo {
        public static Rendering.GraphicsDeviceType graphicsDeviceType = Rendering.GraphicsDeviceType.OpenGLCore;
    }
    public static class QualitySettings { public static int vSyncCount = 2; }
    public class Camera {
        public static event Action<Camera> onPostRender;
        public static int Listeners => onPostRender?.GetInvocationList().Length ?? 0;
        public static void Emit() => onPostRender?.Invoke(new Camera());
    }
    public static class Time {
        public static float realtimeSinceStartup;
        public static int frameCount;
        public static int renderedFrameCount;
    }
    public static class Debug {
        public static readonly List<string> Messages = new List<string>();
        public static void Log(string value) { Messages.Add(value); }
        public static void LogWarning(string value) { Messages.Add(value); }
    }
}
public sealed class Locale { public string Code = "zh-CN"; }
public static class Localization { public static Locale GetLocale() => new Locale(); }
public static class Strings {
    public static readonly Dictionary<string, string> Values = new Dictionary<string, string>();
    public static void Add(string key, string value) { Values.Add(key, value); }
}
public sealed class Game { public static Game Instance; }
public sealed class GameClock {
    public static GameClock Instance;
    public double GetTime() => 42;
}
public static class Components { public static readonly List<object> LiveMinionIdentities = new List<object>(); }
public sealed class SpeedControlScreen {
    public static SpeedControlScreen Instance;
    public bool IsPaused;
    public int Pauses;
    public int Unpauses;
    public void Pause(bool playSound) { IsPaused = true; Pauses++; }
    public void Unpause(bool playSound) { IsPaused = false; Unpauses++; }
}
public static class KCrashReporter {
    public static bool hasCrash;
    public static void ShowDialog(string error, string stack_trace) {}
}
public static class App { public static void QuitCode(int exitCode) {} }
namespace OniHarness.Runtime {
    public sealed class ControlFileSignals : IDisposable {
        public static bool Changed;
        public static int Disposals;
        public ControlFileSignals(string root) {}
        public bool Consume() { bool value = Changed; Changed = false; return value; }
        public void Dispose() { Disposals++; }
    }
    public static class InstanceControl {
        public static int Installs;
        public static void Install(string root) { Installs++; }
    }
    public static class HeadlessVisuals {
        public static int Installs;
        public static int SkippedInitializers;
        public static int DisabledVideoPlayers;
        public static int SkippedTimelapseCaptures;
        public static void Install(HarmonyLib.Harmony harmony) { Installs++; }
    }
}

public static class ContractMain {
    private static int checks;
    private static string root;
    private static void Check(bool value, string label) {
        checks++;
        if (!value) throw new Exception(label);
    }
    private static void Invoke(object instance, string name) {
        try { instance.GetType().GetMethod(name, BindingFlags.NonPublic | BindingFlags.Instance).Invoke(instance, null); }
        catch (TargetInvocationException error) { throw error.InnerException; }
    }
    private static void QuitTrace() {
        typeof(OniHarness.Runtime.Mod).GetMethod("TraceQuit", BindingFlags.NonPublic | BindingFlags.Static).Invoke(null, new object[] { 7 });
    }
    private static JsonElement Status() => JsonDocument.Parse(File.ReadAllText(Path.Combine(root, "status.json"))).RootElement.Clone();
    private static OniHarness.Runtime.RenderController Load(bool headless = false) {
        if (headless) {
            UnityEngine.Application.isBatchMode = true;
            UnityEngine.SystemInfo.graphicsDeviceType = UnityEngine.Rendering.GraphicsDeviceType.Null;
        }
        new OniHarness.Runtime.Mod().OnLoad(new HarmonyLib.Harmony());
        return UnityEngine.GameObject.LastComponent as OniHarness.Runtime.RenderController;
    }
    private static void OriginalSettings() {
        Check(UnityEngine.Rendering.OnDemandRendering.renderFrameInterval == 7, "original render interval preserved");
        Check(UnityEngine.Application.targetFrameRate == 144, "original target rate preserved");
        Check(UnityEngine.QualitySettings.vSyncCount == 2, "original vsync preserved");
        Check(!UnityEngine.Application.runInBackground, "original background setting preserved");
    }
    private static void NoRuntime() {
        Check(UnityEngine.GameObject.Creations == 0, "no runtime GameObject created");
        Check(OniHarness.Runtime.InstanceControl.Installs == 0, "instance controls not installed");
        Check(HarmonyLib.Harmony.ManualPatches == 0, "runtime trace patches not installed");
        Check(OniHarness.Runtime.HeadlessVisuals.Installs == 0, "headless patches not installed");
        Check(UnityEngine.Camera.Listeners == 0, "camera hooks not installed");
        OriginalSettings();
    }
    public static int Main(string[] args) {
        root = Environment.GetEnvironmentVariable("ONIHARNESS_TEST_ROOT");
        string scenario = args[0];
        if (scenario == "inactive") {
            Check(Load() == null, "ordinary launch is inactive");
            Check(PeterHan.PLib.Core.PUtil.Initializations == 1, "PLib initialized for native options button");
            Check(PeterHan.PLib.Options.POptions.Registrations == 1 &&
                PeterHan.PLib.Options.POptions.RegisteredType == typeof(OniHarness.Runtime.RuntimeOptions), "options type registered without test arguments");
            Check(!Directory.Exists(root), "ordinary launch creates no control directory");
            foreach (string key in new[] { "SESSIONS", "REDUCED", "INTERVAL", "UPDATES", "STATUS", "EXITTRACE" }) {
                Check(Strings.Values.TryGetValue("STRINGS.ONIHARNESS.OPTIONS." + key + ".NAME", out string title) &&
                    !string.IsNullOrWhiteSpace(title) && Strings.Values.TryGetValue("STRINGS.ONIHARNESS.OPTIONS." + key + ".DESC", out string description) &&
                    !string.IsNullOrWhiteSpace(description), "each live setting receives a visible title and description");
            }
            NoRuntime();
        } else if (scenario == "disabled") {
            Check(Load() == null, "disabled explicit session has no controller");
            var status = Status();
            Check(status.GetProperty("controlDisabled").GetBoolean(), "disabled receipt explicitly reports its reason");
            Check(status.GetProperty("error").GetString().Contains("disabled"), "disabled receipt is actionable");
            Check(status.GetProperty("schema").GetInt32() == 1 && status.GetProperty("pid").GetInt32() > 0, "disabled receipt keeps normal status envelope");
            Check(DateTimeOffset.Parse(status.GetProperty("capturedAt").GetString()) <= DateTimeOffset.UtcNow, "disabled receipt is timestamped");
            NoRuntime();
        } else if (scenario == "default-reduced") {
            var control = Load();
            Check(control != null && UnityEngine.Application.runInBackground, "explicit session installs background controller");
            Check(OniHarness.Runtime.InstanceControl.Installs == 1 && HarmonyLib.Harmony.ManualPatches == 2, "explicit-only controls and trace hooks installed");
            Invoke(control, "Update");
            var status = Status();
            Check(status.GetProperty("mode").GetString() == "reduced", "GUI default enables reduced rendering only in explicit session");
            Check(status.GetProperty("renderInterval").GetInt32() == 17, "GUI render interval is applied");
            Check(status.GetProperty("updateRate").GetInt32() == 57 && UnityEngine.Application.targetFrameRate == 57, "GUI update rate is applied");
            Check(UnityEngine.QualitySettings.vSyncCount == 0, "reduced mode disables vsync");
            Check(status.GetProperty("error").ValueKind == JsonValueKind.Null, "valid GUI defaults do not produce an error");
            Invoke(control, "OnDestroy");
            OriginalSettings();
            Check(UnityEngine.Camera.Listeners == 0 && OniHarness.Runtime.ControlFileSignals.Disposals == 1, "destroy removes camera and file hooks");
        } else if (scenario == "explicit-overrides") {
            Directory.CreateDirectory(root);
            string render = Path.Combine(root, "render.json");
            File.WriteAllText(render, "{\"Mode\":\"normal\",\"Interval\":3,\"UpdateRate\":59}");
            var control = Load();
            Invoke(control, "Update");
            Check(Status().GetProperty("mode").GetString() == "normal", "explicit normal mode overrides GUI reduced default");
            Check(UnityEngine.Rendering.OnDemandRendering.renderFrameInterval == 7 &&
                UnityEngine.Application.targetFrameRate == 144 && UnityEngine.QualitySettings.vSyncCount == 2, "explicit normal mode retains native rendering");
            File.WriteAllText(render, "{\"Mode\":\"reduced\",\"Interval\":32,\"UpdateRate\":90}");
            OniHarness.Runtime.ControlFileSignals.Changed = true;
            UnityEngine.Time.realtimeSinceStartup = 0.01f;
            Invoke(control, "Update");
            Check(Status().GetProperty("renderInterval").GetInt32() == 32 && UnityEngine.Application.targetFrameRate == 90, "control notification immediately applies explicit values despite long reconciliation interval");
            File.WriteAllText(render, "{\"Mode\":\"reduced\",\"Interval\":999,\"UpdateRate\":90}");
            OniHarness.Runtime.ControlFileSignals.Changed = true;
            Invoke(control, "Update");
            Check(Status().GetProperty("error").GetString().Contains("interval"), "invalid explicit file is rejected, not clamped into success");
            Invoke(control, "OnDestroy");
            OriginalSettings();
        } else if (scenario == "headless") {
            Directory.CreateDirectory(root);
            File.WriteAllText(Path.Combine(root, "render.json"), "{\"Mode\":\"normal\",\"Interval\":9,\"UpdateRate\":75}");
            var control = Load(headless: true);
            Invoke(control, "Update");
            var status = Status();
            Check(status.GetProperty("headless").GetBoolean() && status.GetProperty("headlessRequested").GetBoolean(), "Null device stays in requested headless mode");
            Check(status.GetProperty("mode").GetString() == "headless" && status.GetProperty("renderInterval").GetInt32() == 1, "explicit normal file cannot turn headless into graphical mode");
            Check(UnityEngine.Application.isBatchMode && UnityEngine.SystemInfo.graphicsDeviceType == UnityEngine.Rendering.GraphicsDeviceType.Null, "options never create a graphics device");
            Check(OniHarness.Runtime.HeadlessVisuals.Installs == 1 && UnityEngine.Application.runInBackground, "headless safeguards and background operation remain active");
            Invoke(control, "OnDestroy");
            OriginalSettings();
        } else if (scenario == "headless-rejected") {
            var control = Load();
            Invoke(control, "Update");
            var status = Status();
            Check(!status.GetProperty("headless").GetBoolean(), "graphical fixture is not mislabeled as headless");
            Check(status.GetProperty("error").GetString().Contains("Null graphics device"), "headless request without Null device is a failure");
            Check(OniHarness.Runtime.HeadlessVisuals.Installs == 0, "headless-only visual patches not installed on graphical device");
            Invoke(control, "OnDestroy");
            OriginalSettings();
        } else if (scenario == "normalize") {
            var low = new OniHarness.Runtime.RuntimeOptions { ReducedRenderInterval = -9, ReducedUpdateRate = -9, StatusIntervalSeconds = -9 }.Normalize();
            Check(low.ReducedRenderInterval == 1 && low.ReducedUpdateRate == 15 && low.StatusIntervalSeconds == .25, "lower bounds applied to persistent GUI settings");
            var high = new OniHarness.Runtime.RuntimeOptions { ReducedRenderInterval = int.MaxValue, ReducedUpdateRate = int.MaxValue, StatusIntervalSeconds = 999 }.Normalize();
            Check(high.ReducedRenderInterval == 120 && high.ReducedUpdateRate == 240 && high.StatusIntervalSeconds == 2, "upper bounds applied with margin below the five-second client freshness limit");
            foreach (double value in new[] { double.NaN, double.PositiveInfinity, double.NegativeInfinity }) {
                var nonFinite = new OniHarness.Runtime.RuntimeOptions { StatusIntervalSeconds = value }.Normalize();
                Check(nonFinite.StatusIntervalSeconds == 1, "nonfinite status interval resets to safe default");
            }
            NoRuntime();
        } else if (scenario == "malformed-options") {
            Check(Load() == null, "malformed options do not enable an ordinary session");
            var defaults = OniHarness.Runtime.RuntimeOptions.Current;
            Check(defaults.AllowExplicitSessions && !defaults.ReduceRenderingByDefault && !defaults.LogExitStackTrace, "malformed config restores boolean defaults");
            Check(defaults.ReducedRenderInterval == 60 && defaults.ReducedUpdateRate == 30 && defaults.StatusIntervalSeconds == 1, "malformed config restores numeric defaults");
            Check(UnityEngine.Debug.Messages.Exists(text => text.Contains("Could not read runtime options")), "malformed config emits a useful warning");
            Check(!Directory.Exists(root), "malformed options create no control directory without authorization");
            NoRuntime();
        } else if (scenario == "deadline") {
            Game.Instance = new Game();
            GameClock.Instance = new GameClock();
            SpeedControlScreen.Instance = new SpeedControlScreen();
            var control = Load();
            File.WriteAllText(Path.Combine(root, "run-lease.json"), JsonSerializer.Serialize(new { Id = "expired", DeadlineUtc = DateTimeOffset.UtcNow.AddSeconds(-1) }));
            Invoke(control, "Update");
            Check(!SpeedControlScreen.Instance.IsPaused, "lease loaded for the per-frame watchdog");
            UnityEngine.Time.realtimeSinceStartup = .01f;
            Invoke(control, "Update");
            Check(SpeedControlScreen.Instance.IsPaused && SpeedControlScreen.Instance.Pauses == 1, "expired lease pauses before two-second status interval elapses");
            UnityEngine.Time.realtimeSinceStartup = .02f;
            Invoke(control, "Update");
            Check(SpeedControlScreen.Instance.Pauses == 1, "watchdog does not acquire duplicate native pause holds");
            Invoke(control, "OnDestroy");
            OriginalSettings();
        } else if (scenario == "exit-trace") {
            Load();
            QuitTrace();
            Check(!UnityEngine.Debug.Messages[UnityEngine.Debug.Messages.Count - 1].Contains("\n"), "exit stack diagnostics are off by default");
            Environment.SetEnvironmentVariable("ONIHARNESS_TEST_OPTIONS", "{\"LogExitStackTrace\":true}");
            OniHarness.Runtime.RuntimeOptions.Load();
            QuitTrace();
            Check(UnityEngine.Debug.Messages[UnityEngine.Debug.Messages.Count - 1].Contains("\n"), "explicit stack diagnostics include trace");
            NoRuntime();
        } else throw new Exception("unknown scenario " + scenario);
        Console.WriteLine("runtime-options " + scenario + ": " + checks + " checks passed");
        return 0;
    }
}
'''


class RuntimeOptionsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if shutil.which("dotnet") is None:
            raise unittest.SkipTest("dotnet SDK is required for game-free production contracts")
        cls.build = tempfile.TemporaryDirectory(prefix="oniharness-runtime-options-")
        cls.addClassCleanup(cls.build.cleanup)
        folder = Path(cls.build.name)
        sources = "\n".join(
            f'<Compile Include="{html.escape(str(RUNTIME / name), quote=True)}" Link="{name}" />'
            for name in ("Mod.cs", "RuntimeOptions.cs")
        )
        (folder / "Contract.csproj").write_text(
            '<Project Sdk="Microsoft.NET.Sdk"><PropertyGroup>'
            '<OutputType>Exe</OutputType><TargetFramework>net10.0</TargetFramework>'
            '<EnableDefaultCompileItems>false</EnableDefaultCompileItems>'
            '<ImplicitUsings>disable</ImplicitUsings><Nullable>disable</Nullable>'
            '<TreatWarningsAsErrors>true</TreatWarningsAsErrors>'
            '</PropertyGroup><ItemGroup><Compile Include="Doubles.cs" />'
            f'{sources}</ItemGroup></Project>', encoding="utf-8"
        )
        (folder / "Doubles.cs").write_text(DOUBLES, encoding="utf-8")
        result = subprocess.run(
            ["dotnet", "build", str(folder / "Contract.csproj"), "-c", "Release", "--nologo"],
            capture_output=True, text=True, timeout=120,
        )
        if result.returncode:
            raise AssertionError(result.stdout + result.stderr)
        cls.dll = folder / "bin/Release/net10.0/Contract.dll"

    def run_case(self, scenario, options=None, *, explicit=True, headless=False):
        with tempfile.TemporaryDirectory(prefix="oniharness-options-case-") as temp:
            root = Path(temp) / "control"
            env = dict(os.environ, ONIHARNESS_TEST_ROOT=str(root))
            if options is not None:
                env["ONIHARNESS_TEST_OPTIONS"] = options if isinstance(options, str) else json.dumps(options)
            else:
                env.pop("ONIHARNESS_TEST_OPTIONS", None)
            command = ["dotnet", str(self.dll), scenario]
            if explicit:
                command.extend(["--oniharness-control", str(root)])
            if headless:
                command.append("--oniharness-headless")
            result = subprocess.run(command, env=env, capture_output=True, text=True, timeout=30)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertIn(f"runtime-options {scenario}:", result.stdout)

    def test_ordinary_launch_registers_options_without_starting_controls(self):
        self.run_case("inactive", {"ReduceRenderingByDefault": True}, explicit=False)

    def test_disabled_explicit_session_reports_reason_without_starting_controls(self):
        self.run_case("disabled", {"AllowExplicitSessions": False})

    def test_gui_reduced_defaults_apply_and_destroy_restores_original_state(self):
        self.run_case("default-reduced", {"ReduceRenderingByDefault": True,
            "ReducedRenderInterval": 17, "ReducedUpdateRate": 57})

    def test_explicit_control_file_overrides_gui_and_invalid_file_stays_failure(self):
        self.run_case("explicit-overrides", {"ReduceRenderingByDefault": True,
            "ReducedRenderInterval": 17, "ReducedUpdateRate": 57, "StatusIntervalSeconds": 2})

    def test_gui_and_explicit_normal_mode_cannot_downgrade_headless(self):
        self.run_case("headless", {"ReduceRenderingByDefault": False}, headless=True)

    def test_graphics_device_cannot_claim_requested_headless_success(self):
        self.run_case("headless-rejected", headless=True)

    def test_numeric_option_bounds_and_nonfinite_interval(self):
        self.run_case("normalize", explicit=False)

    def test_malformed_settings_fall_back_without_authorizing_control(self):
        self.run_case("malformed-options", "{invalid", explicit=False)

    def test_long_status_interval_does_not_delay_native_lease_pause(self):
        self.run_case("deadline", {"StatusIntervalSeconds": 2})

    def test_exit_stack_diagnostics_require_explicit_option(self):
        self.run_case("exit-trace", explicit=False)


if __name__ == "__main__":
    unittest.main()
