using System;
using PeterHan.PLib.Options;
using UnityEngine;

namespace OniHarness.Runtime
{
    [ConfigFile("OniHarnessRuntimeOptions.json", true)]
    public sealed class RuntimeOptions
    {
        internal static RuntimeOptions Current { get; private set; } = new RuntimeOptions();

        [Option("STRINGS.ONIHARNESS.OPTIONS.SESSIONS.NAME", "STRINGS.ONIHARNESS.OPTIONS.SESSIONS.DESC")]
        [RestartRequired]
        public bool AllowExplicitSessions { get; set; } = true;

        [Option("STRINGS.ONIHARNESS.OPTIONS.REDUCED.NAME", "STRINGS.ONIHARNESS.OPTIONS.REDUCED.DESC")]
        [RestartRequired]
        public bool ReduceRenderingByDefault { get; set; }

        [Option("STRINGS.ONIHARNESS.OPTIONS.INTERVAL.NAME", "STRINGS.ONIHARNESS.OPTIONS.INTERVAL.DESC")]
        [Limit(1, 120)]
        [RestartRequired]
        public int ReducedRenderInterval { get; set; } = 60;

        [Option("STRINGS.ONIHARNESS.OPTIONS.UPDATES.NAME", "STRINGS.ONIHARNESS.OPTIONS.UPDATES.DESC")]
        [Limit(15, 240)]
        [RestartRequired]
        public int ReducedUpdateRate { get; set; } = 30;

        [Option("STRINGS.ONIHARNESS.OPTIONS.STATUS.NAME", "STRINGS.ONIHARNESS.OPTIONS.STATUS.DESC")]
        [Limit(0.25, 2)]
        [RestartRequired]
        public double StatusIntervalSeconds { get; set; } = 1;

        [Option("STRINGS.ONIHARNESS.OPTIONS.EXITTRACE.NAME", "STRINGS.ONIHARNESS.OPTIONS.EXITTRACE.DESC")]
        [RestartRequired]
        public bool LogExitStackTrace { get; set; }

        internal static void Load()
        {
            try { Current = (POptions.ReadSettings<RuntimeOptions>() ?? new RuntimeOptions()).Normalize(); }
            catch (Exception ex)
            {
                Current = new RuntimeOptions();
                Debug.LogWarning("[OniHarness] Could not read runtime options; using defaults: " + ex.Message);
            }
        }

        internal RuntimeOptions Normalize()
        {
            ReducedRenderInterval = Math.Max(1, Math.Min(120, ReducedRenderInterval));
            ReducedUpdateRate = Math.Max(15, Math.Min(240, ReducedUpdateRate));
            StatusIntervalSeconds = double.IsNaN(StatusIntervalSeconds) || double.IsInfinity(StatusIntervalSeconds)
                ? 1 : Math.Max(0.25, Math.Min(2, StatusIntervalSeconds));
            return this;
        }

        internal static void RegisterText()
        {
            bool chinese = (Localization.GetLocale()?.Code ?? "").StartsWith("zh", StringComparison.OrdinalIgnoreCase);
            Add("SESSIONS", chinese ? "允许专门启动的测试会话" : "Allow explicitly launched test sessions",
                chinese ? "仍需 --oniharness-control 启动参数。普通游戏不会自动开启控制；关闭后拒绝下次测试启动。" :
                "Still requires --oniharness-control. Ordinary launches never enable test control; disabling rejects the next test launch.");
            Add("REDUCED", chinese ? "默认降低测试绘图频率" : "Reduce test rendering by default",
                chinese ? "仅影响未提供 render.json 的图形测试。显式控制文件优先，不改变模拟逻辑；无头测试仍保持无图形设备。" :
                "Only graphical tests without render.json. Explicit control files take precedence; simulation and Null-device headless mode are unchanged.");
            Add("INTERVAL", chinese ? "降帧时的绘制间隔" : "Reduced-mode render interval",
                chinese ? "每隔多少次更新绘制一帧，1–120。仅默认降帧模式生效；控制文件可指定本次值。" :
                "Render once every 1–120 updates in default reduced mode. A control file can override the value for one run.");
            Add("UPDATES", chinese ? "降帧时的更新上限" : "Reduced-mode update limit",
                chinese ? "默认降帧模式的每秒更新上限，15–240；不是模拟加速倍率。" :
                "Update limit in default reduced mode, 15–240 per second. This is not a simulation speed multiplier.");
            Add("STATUS", chinese ? "状态检查间隔（秒）" : "Status reconciliation interval (seconds)",
                chinese ? "0.25–2 秒。控制文件变化仍立即响应，运行截止时间仍每帧检查；测试会话保持后台运行。" :
                "0.25–2 seconds. File changes wake immediately and run deadlines are checked every frame; test sessions keep running in the background.");
            Add("EXITTRACE", chinese ? "记录退出堆栈" : "Log exit stack traces",
                chinese ? "仅在专门启动的测试会话退出时记录额外诊断，默认关闭；不会上传日志。" :
                "Extra local diagnostics when an explicit test session exits. Off by default; logs are never uploaded.");
        }

        private static void Add(string key, string title, string description)
        {
            Strings.Add("STRINGS.ONIHARNESS.OPTIONS." + key + ".NAME", title);
            Strings.Add("STRINGS.ONIHARNESS.OPTIONS." + key + ".DESC", description);
        }
    }
}
