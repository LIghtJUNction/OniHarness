using System;
using System.Collections.Generic;
using Unity.Profiling;
using Unity.Profiling.LowLevel.Unsafe;
using UnityEngine;

namespace OniHarness.Runtime
{
    // Explicit diagnostic mode: native counters, without patching game methods.
    internal sealed class EngineTimingProbe : IDisposable
    {
        private sealed class Entry
        {
            internal string Name, Unit;
            internal ProfilerRecorder Recorder;
        }

        private static readonly HashSet<string> Wanted = new HashSet<string>(StringComparer.Ordinal) {
            "Main Thread", "Render Thread", "PlayerLoop", "RenderLoop",
            "Gfx.WaitForPresentOnGfxThread", "GfxDevice.WaitForRenderThread", "Gfx.PresentFrame",
            "WaitForTargetFPS", "GC.Collect", "GC Used Memory", "GC Reserved Memory",
            "Total Used Memory", "Total Reserved Memory", "System Used Memory",
            "CPU Total Frame Time", "CPU Main Thread Frame Time", "CPU Render Thread Frame Time", "GPU Frame Time"
        };
        private readonly List<Entry> entries = new List<Entry>();
        private readonly FrameTiming[] frame = new FrameTiming[1];
        private string error;

        internal EngineTimingProbe()
        {
            try
            {
                var handles = new List<ProfilerRecorderHandle>();
                ProfilerRecorderHandle.GetAvailable(handles);
                foreach (var handle in handles)
                {
                    var description = ProfilerRecorderHandle.GetDescription(handle);
                    if (!Wanted.Contains(description.Name)) continue;
                    var recorder = ProfilerRecorder.StartNew(description.Category, description.Name, 120);
                    if (!recorder.Valid) { recorder.Dispose(); continue; }
                    entries.Add(new Entry { Name = description.Name, Unit = description.UnitType.ToString(), Recorder = recorder });
                }
            }
            catch (Exception ex) { error = ex.Message; }
        }

        internal void Capture()
        {
            if (error != null) return;
            try
            {
                if (FrameTimingManager.IsFeatureEnabled()) FrameTimingManager.CaptureFrameTimings();
            }
            catch (Exception ex) { error = ex.Message; }
        }

        internal object Snapshot()
        {
            try { return SnapshotCore(); }
            catch (Exception ex)
            {
                error = ex.Message;
                return new { enabled = true, available = false, error };
            }
        }

        private object SnapshotCore()
        {
            var counters = new List<object>(entries.Count);
            foreach (var entry in entries)
            {
                long? sample = entry.Recorder.Count == 0 ? (long?)null : entry.Recorder.LastValue;
                if (entry.Name == "GPU Frame Time" && sample <= 0) sample = null;
                counters.Add(new { name = entry.Name, unit = entry.Unit,
                    samples = entry.Recorder.Count,
                    value = sample });
            }
            object timing = null;
            bool enabled = FrameTimingManager.IsFeatureEnabled();
            if (enabled && FrameTimingManager.GetLatestTimings(1, frame) != 0)
            {
                var value = frame[0];
                timing = new { cpuFrameMs = value.cpuFrameTime, cpuMainMs = value.cpuMainThreadFrameTime,
                    cpuRenderMs = value.cpuRenderThreadFrameTime, presentWaitMs = value.cpuMainThreadPresentWaitTime,
                    gpuMs = value.gpuFrameTime > 0 && FrameTimingManager.GetGpuTimerFrequency() != 0
                        ? (double?)value.gpuFrameTime : null, frameStartTimestamp = value.frameStartTimestamp };
            }
            return new { enabled = true, available = entries.Count != 0 || enabled, frameTimingFeatureEnabled = enabled,
                frameTiming = timing, counters, error };
        }

        public void Dispose()
        {
            foreach (var entry in entries) entry.Recorder.Dispose();
            entries.Clear();
        }
    }
}
