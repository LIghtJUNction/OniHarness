using System;
using System.IO;
using HarmonyLib;
using KMod;
using Newtonsoft.Json;
using UnityEngine;
using UnityEngine.Rendering;

namespace OniHarness.Runtime
{
    public sealed class Mod : UserMod2
    {
        public override void OnLoad(Harmony harmony)
        {
            base.OnLoad(harmony);
            var args = Environment.GetCommandLineArgs();
            int index = Array.IndexOf(args, "--oniharness-control");
            // Installing the companion alone must not change normal gameplay.
            if (index < 0 || index + 1 >= args.Length) return;
            var root = Path.GetFullPath(args[index + 1]);
            Directory.CreateDirectory(root);
            var owner = new GameObject("OniHarness.Runtime");
            UnityEngine.Object.DontDestroyOnLoad(owner);
            owner.AddComponent<RenderController>().Initialize(root);
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
        private int cameraRenderCallbacks;
        private int cameraRenderFrames;
        private int lastCameraFrame = -1;
        private int pid;

        public void Initialize(string directory)
        {
            root = directory;
            using (var process = System.Diagnostics.Process.GetCurrentProcess()) pid = process.Id;
            originalInterval = OnDemandRendering.renderFrameInterval;
            originalBackground = Application.runInBackground;
            originalTargetRate = Application.targetFrameRate;
            originalVSync = QualitySettings.vSyncCount;
            Application.runInBackground = true;
            Camera.onPostRender += RecordCameraRender;
            Debug.Log("[OniHarness] Runtime companion active; simulation remains native.");
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
                SpeedControlScreen.Instance.Pause();
            if (root == null || Time.realtimeSinceStartup < nextCheck) return;
            nextCheck = Time.realtimeSinceStartup + 1f;
            try
            {
                var path = Path.Combine(root, "render.json");
                var settings = File.Exists(path)
                    ? JsonConvert.DeserializeObject<RenderSettings>(File.ReadAllText(path))
                    : new RenderSettings();
                if (settings == null || (settings.Mode != "normal" && settings.Mode != "reduced") ||
                    settings.Interval < 1 || settings.Interval > 120 || settings.UpdateRate < 15 || settings.UpdateRate > 240)
                    throw new InvalidDataException("Mode must be normal/reduced, interval 1..120 and update rate 15..240.");
                mode = settings.Mode;
                updateRate = settings.UpdateRate;
                OnDemandRendering.renderFrameInterval = mode == "normal" ? originalInterval : settings.Interval;
                QualitySettings.vSyncCount = mode == "normal" ? originalVSync : 0;
                Application.targetFrameRate = mode == "normal" ? originalTargetRate : updateRate;
                error = null;
            }
            catch (Exception ex)
            {
                // A malformed control file restores the original rendering policy.
                mode = "normal";
                OnDemandRendering.renderFrameInterval = originalInterval;
                QualitySettings.vSyncCount = originalVSync;
                Application.targetFrameRate = originalTargetRate;
                error = ex.Message;
            }
            try
            {
                var leasePath = Path.Combine(root, "run-lease.json");
                lease = File.Exists(leasePath) ? JsonConvert.DeserializeObject<RunLease>(File.ReadAllText(leasePath)) : null;
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
            }
            catch (Exception ex)
            {
                Debug.LogWarning("[OniHarness] Status write failed: " + ex.Message);
            }
        }

        private void OnDestroy()
        {
            if (root == null) return;
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
