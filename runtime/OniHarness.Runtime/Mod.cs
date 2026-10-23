using System;
using System.IO;
using HarmonyLib;
using KMod;
using Newtonsoft.Json;
using PeterHan.PLib.Core;
using PeterHan.PLib.Options;
using UnityEngine;
using UnityEngine.Rendering;

namespace OniHarness.Runtime
{
    public sealed class Mod : UserMod2
    {
        private static string controlRoot;
        internal static string GameError;

        public override void OnLoad(Harmony harmony)
        {
            PUtil.InitLibrary();
            RuntimeOptions.RegisterText();
            base.OnLoad(harmony);
            new POptions().RegisterOptions(this, typeof(RuntimeOptions));
            RuntimeOptions.Load();
            var args = Environment.GetCommandLineArgs();
            int index = Array.IndexOf(args, "--oniharness-control");
            // Installing the companion alone must not change normal gameplay.
            if (index < 0 || index + 1 >= args.Length) return;
            var root = Path.GetFullPath(args[index + 1]);
            Directory.CreateDirectory(root);
            controlRoot = root;
            if (!RuntimeOptions.Current.AllowExplicitSessions)
            {
                RejectDisabledSession(root);
                return;
            }
            InstanceControl.Install(root);
            bool headless = Array.IndexOf(args, "--oniharness-headless") >= 0;
            if (headless && Application.isBatchMode && SystemInfo.graphicsDeviceType == GraphicsDeviceType.Null)
                HeadlessVisuals.Install(harmony);
            harmony.Patch(AccessTools.Method(typeof(KCrashReporter), "ShowDialog"),
                prefix: new HarmonyMethod(typeof(Mod), nameof(TraceError)));
            harmony.Patch(AccessTools.Method(typeof(App), "QuitCode"),
                prefix: new HarmonyMethod(typeof(Mod), nameof(TraceQuit)));
            var owner = new GameObject("OniHarness.Runtime");
            UnityEngine.Object.DontDestroyOnLoad(owner);
            owner.AddComponent<RenderController>().Initialize(root, headless);
        }

        private static void TraceQuit(int exitCode)
        {
            Debug.LogWarning("[OniHarness] Exit " + exitCode + "; crash=" + KCrashReporter.hasCrash +
                (RuntimeOptions.Current.LogExitStackTrace ? "\n" + Environment.StackTrace : ""));
        }

        private static void RejectDisabledSession(string root)
        {
            const string message = "Explicit test sessions are disabled in OniHarness Runtime mod options. Enable them and restart through the test launcher.";
            Debug.LogWarning("[OniHarness] " + message);
            using (var process = System.Diagnostics.Process.GetCurrentProcess())
            {
                var receipt = JsonConvert.SerializeObject(new
                {
                    schema = 1, capturedAt = System.DateTime.UtcNow.ToString("o"), pid = process.Id,
                    controlDisabled = true, error = message
                });
                var temporary = Path.Combine(root, "status.tmp");
                File.WriteAllText(temporary, receipt);
                var destination = Path.Combine(root, "status.json");
                if (File.Exists(destination)) File.Replace(temporary, destination, null);
                else File.Move(temporary, destination);
            }
        }

        private static void TraceError(string error, string stack_trace)
        {
            if (GameError != null) return;
            GameError = error;
            try
            {
                File.WriteAllText(Path.Combine(controlRoot, "failure.json"), JsonConvert.SerializeObject(new
                {
                    capturedAt = System.DateTime.UtcNow.ToString("o"), error, stackTrace = stack_trace
                }));
            }
            catch (Exception ex) { Debug.LogWarning("[OniHarness] Failure receipt write failed: " + ex.Message); }
        }
    }

    public sealed class RenderController : MonoBehaviour
    {
        private string root;
        private int originalInterval;
        private bool originalBackground;
        private int originalTargetRate;
        private int originalVSync;
        private int updateRate = 30;
        private float nextCheck;
        private string mode = "normal";
        private string error;
        private RunLease lease;
        private SpeedControlScreen deadlinePauseScreen;
        private int cameraRenderCallbacks;
        private int cameraRenderFrames;
        private int lastCameraFrame = -1;
        private int pid;
        private bool headlessRequested;
        private bool headless;
        private ControlFileSignals controls;
        private bool statusWritten;
        private Game lastGame;
        private bool? lastPaused;
        private int lastDuplicants;

        public void Initialize(string directory, bool requireHeadless)
        {
            root = directory;
            headlessRequested = requireHeadless;
            headless = Application.isBatchMode && SystemInfo.graphicsDeviceType == GraphicsDeviceType.Null;
            using (var process = System.Diagnostics.Process.GetCurrentProcess()) pid = process.Id;
            originalInterval = OnDemandRendering.renderFrameInterval;
            originalBackground = Application.runInBackground;
            originalTargetRate = Application.targetFrameRate;
            originalVSync = QualitySettings.vSyncCount;
            Application.runInBackground = true;
            try { controls = new ControlFileSignals(root); }
            catch (Exception ex) { Debug.LogWarning("[OniHarness] Control notifications unavailable; keeping 1s polling: " + ex.Message); }
            Camera.onPostRender += RecordCameraRender;
            Debug.Log("[OniHarness] Runtime companion active; headless=" + headless + "; simulation remains native.");
        }

        private void RecordCameraRender(Camera camera)
        {
            cameraRenderCallbacks++;
            if (lastCameraFrame == Time.frameCount) return;
            lastCameraFrame = Time.frameCount;
            cameraRenderFrames++;
        }

        private void Update()
        {
            // The game owns the stop deadline, so a lost test client cannot leave it running.
            if (lease != null && System.DateTimeOffset.UtcNow >= lease.DeadlineUtc &&
                SpeedControlScreen.Instance != null && !SpeedControlScreen.Instance.IsPaused)
            {
                SpeedControlScreen.Instance.Pause(playSound: false);
                deadlinePauseScreen = SpeedControlScreen.Instance;
            }
            if (root == null) return;
            bool changed = controls != null && controls.Consume();
            bool? pausedNow = SpeedControlScreen.Instance == null ? (bool?)null : SpeedControlScreen.Instance.IsPaused;
            bool transition = !statusWritten || !ReferenceEquals(lastGame, Game.Instance) ||
                lastPaused != pausedNow || lastDuplicants != Components.LiveMinionIdentities.Count;
            // Wake promptly for a new lease or pause transition, but keep the
            // periodic reconciliation if file notifications are delayed/lost.
            if (!changed && !transition && Time.realtimeSinceStartup < nextCheck) return;
            nextCheck = Time.realtimeSinceStartup + (float)RuntimeOptions.Current.StatusIntervalSeconds;
            try
            {
                var path = Path.Combine(root, "render.json");
                var settings = File.Exists(path)
                    ? JsonConvert.DeserializeObject<RenderSettings>(File.ReadAllText(path))
                    : new RenderSettings
                    {
                        Mode = RuntimeOptions.Current.ReduceRenderingByDefault ? "reduced" : "normal",
                        Interval = RuntimeOptions.Current.ReducedRenderInterval,
                        UpdateRate = RuntimeOptions.Current.ReducedUpdateRate
                    };
                if (settings == null || (settings.Mode != "normal" && settings.Mode != "reduced" && settings.Mode != "headless") ||
                    settings.Interval < 1 || settings.Interval > 120 || settings.UpdateRate < 15 || settings.UpdateRate > 240)
                    throw new InvalidDataException("Mode must be normal/reduced/headless, interval 1..120 and update rate 15..240.");
                if ((headlessRequested || settings.Mode == "headless") && !headless)
                    throw new InvalidDataException("Headless requires batch mode and a Null graphics device; no graphical fallback.");
                mode = headless ? "headless" : settings.Mode;
                updateRate = settings.UpdateRate;
                OnDemandRendering.renderFrameInterval = headless ? 1 : mode == "normal" ? originalInterval : settings.Interval;
                QualitySettings.vSyncCount = mode == "normal" ? originalVSync : 0;
                Application.targetFrameRate = mode == "normal" ? originalTargetRate : updateRate;
                error = null;
            }
            catch (Exception ex)
            {
                // Policy cannot create a graphics device in a headless process.
                mode = headless ? "headless" : "normal";
                OnDemandRendering.renderFrameInterval = originalInterval;
                QualitySettings.vSyncCount = originalVSync;
                Application.targetFrameRate = originalTargetRate;
                error = ex.Message;
            }
            if (KCrashReporter.hasCrash || Mod.GameError != null)
                error = Mod.GameError ?? "The native game reported a crash; inspect player.log.";
            if (error != null && SpeedControlScreen.Instance != null && !SpeedControlScreen.Instance.IsPaused)
                SpeedControlScreen.Instance.Pause(playSound: false);
            try
            {
                var leasePath = Path.Combine(root, "run-lease.json");
                var nextLease = File.Exists(leasePath) ? JsonConvert.DeserializeObject<RunLease>(File.ReadAllText(leasePath)) : null;
                if (nextLease != null && nextLease.Id != lease?.Id && nextLease.DeadlineUtc > System.DateTimeOffset.UtcNow &&
                    deadlinePauseScreen != null)
                {
                    // Native pauses are counted. Release only the hold this companion acquired.
                    if (ReferenceEquals(deadlinePauseScreen, SpeedControlScreen.Instance))
                        deadlinePauseScreen.Unpause(playSound: false);
                    deadlinePauseScreen = null;
                }
                lease = nextLease;
            }
            catch (Exception ex)
            {
                lease = new RunLease { DeadlineUtc = System.DateTimeOffset.MinValue };
                error = "Invalid run lease: " + ex.Message;
            }
            try
            {
                var clock = GameClock.Instance;
                var status = new
                {
                    schema = 1,
                    capturedAt = System.DateTime.UtcNow.ToString("o"),
                    pid,
                    unity = Application.unityVersion,
                    graphicsDevice = SystemInfo.graphicsDeviceType.ToString(),
                    batchMode = Application.isBatchMode,
                    headlessRequested,
                    headless,
                    gameHasCrash = KCrashReporter.hasCrash,
                    gameError = Mod.GameError,
                    skippedVisualInitializers = HeadlessVisuals.SkippedInitializers,
                    disabledVideoPlayers = HeadlessVisuals.DisabledVideoPlayers,
                    skippedTimelapseCaptures = HeadlessVisuals.SkippedTimelapseCaptures,
                    controlNotifications = controls != null,
                    mode,
                    renderInterval = OnDemandRendering.renderFrameInterval,
                    updateRate,
                    targetFrameRate = Application.targetFrameRate,
                    vSyncCount = QualitySettings.vSyncCount,
                    frameCount = Time.frameCount,
                    renderedFrameCount = Time.renderedFrameCount,
                    cameraRenderCallbacks,
                    cameraRenderFrames,
                    runInBackground = Application.runInBackground,
                    gameLoaded = Game.Instance != null,
                    paused = SpeedControlScreen.Instance == null ? (bool?)null : SpeedControlScreen.Instance.IsPaused,
                    simulationSeconds = clock == null ? (double?)null : clock.GetTime(),
                    duplicants = Components.LiveMinionIdentities.Count,
                    leaseId = lease?.Id,
                    leaseDeadlineUtc = lease?.DeadlineUtc,
                    error
                };
                var temp = Path.Combine(root, "status.tmp");
                File.WriteAllText(temp, JsonConvert.SerializeObject(status));
                var destination = Path.Combine(root, "status.json");
                if (File.Exists(destination)) File.Replace(temp, destination, null);
                else File.Move(temp, destination);
                statusWritten = true;
                lastGame = Game.Instance;
                lastPaused = SpeedControlScreen.Instance == null ? (bool?)null : SpeedControlScreen.Instance.IsPaused;
                lastDuplicants = Components.LiveMinionIdentities.Count;
            }
            catch (Exception ex)
            {
                Debug.LogWarning("[OniHarness] Status write failed: " + ex.Message);
            }
        }

        private void OnDestroy()
        {
            if (root == null) return;
            controls?.Dispose();
            Camera.onPostRender -= RecordCameraRender;
            OnDemandRendering.renderFrameInterval = originalInterval;
            QualitySettings.vSyncCount = originalVSync;
            Application.targetFrameRate = originalTargetRate;
            Application.runInBackground = originalBackground;
        }

        private sealed class RenderSettings
        {
            public string Mode { get; set; } = "normal";
            public int Interval { get; set; } = 60;
            public int UpdateRate { get; set; } = 30;
        }

        private sealed class RunLease
        {
            public string Id { get; set; }
            public System.DateTimeOffset DeadlineUtc { get; set; }
        }
    }
}
