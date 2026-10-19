using HarmonyLib;
using System.Reflection;
using UnityEngine;
using UnityEngine.Video;

namespace OniHarness.Runtime
{
    internal static class HeadlessVisuals
    {
        internal static int SkippedInitializers;
        internal static int DisabledVideoPlayers;

        internal static void Install(Harmony harmony)
        {
            var uiFactory = RuntimeMethods.RequireNonGeneric(typeof(Util), "KInstantiateUI",
                typeof(GameObject), typeof(GameObject), typeof(bool));
            // The gamepad cursor uses currentResolution (zero in batch mode),
            // and menu videos initialize shaders unsupported by the Null device.
            // These are presentation entrypoints; no simulation methods are patched.
            foreach (var target in new[]
            {
                AccessTools.Method(typeof(VirtualCursorOverlayFix), "Awake"),
                AccessTools.Method(typeof(MainMenu), "SpawnVideoScreen"),
                AccessTools.Method(typeof(MainMenuIntroShort), "OnSpawn"),
                AccessTools.Method(typeof(MRTLiquidInterception), "Start")
            })
                harmony.Patch(target, prefix: new HarmonyMethod(typeof(HeadlessVisuals), nameof(SkipVisualInit)));
            harmony.Patch(uiFactory,
                prefix: new HarmonyMethod(typeof(HeadlessVisuals), nameof(PrepareVideoUi)));
        }

        private static void PrepareVideoUi(GameObject original)
        {
            if (original == null) return;
            // In-game scheduled UI also clones video prefabs. Deactivate only
            // their video child before native cloning can initialize shaders.
            foreach (var player in original.GetComponentsInChildren<VideoPlayer>(true))
            {
                player.enabled = false;
                player.playOnAwake = false;
                player.targetTexture = null;
                player.renderMode = VideoRenderMode.APIOnly;
                player.gameObject.SetActive(false);
                DisabledVideoPlayers++;
                Debug.Log("[OniHarness] Disabled headless UI video player in " + original.name);
            }
        }

        private static bool SkipVisualInit(MethodBase __originalMethod)
        {
            SkippedInitializers++;
            Debug.Log("[OniHarness] Skipped headless visual initialization: " +
                __originalMethod.DeclaringType.Name + "." + __originalMethod.Name);
            return false;
        }
    }
}
