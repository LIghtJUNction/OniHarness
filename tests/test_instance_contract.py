#!/usr/bin/env python3
"""Execute the production companion against explicit game-free API doubles.

These checks do not launch Unity/Steam and do not establish native compatibility.
They exercise nonce routing, save-copy authorization, command replay and watchdog
behavior; the separate companion build checks installed ONI API signatures.
"""
import pathlib
import shutil
import subprocess
import tempfile
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
SOURCE = ROOT / "runtime/OniHarness.Runtime/InstanceControl.cs"

DOUBLES = r'''
using System;
using System.IO;
using System.Reflection;
using System.Text.Json;
using System.Security.Cryptography;
using System.Collections.Generic;

namespace Newtonsoft.Json {
    public sealed class JsonPropertyAttribute : Attribute { public JsonPropertyAttribute(string name) {} }
    public class JsonException : Exception { public JsonException(string text) : base(text) {} }
    public static class JsonConvert {
        private static readonly JsonSerializerOptions options = new JsonSerializerOptions { PropertyNameCaseInsensitive=true, PropertyNamingPolicy=JsonNamingPolicy.CamelCase };
        public static T DeserializeObject<T>(string text) { try { return JsonSerializer.Deserialize<T>(text, options); } catch(System.Text.Json.JsonException e) { throw new JsonException(e.Message); } }
        public static string SerializeObject(object value) => JsonSerializer.Serialize(value, options);
    }
}
namespace UnityEngine {
    public class Object { public static void DontDestroyOnLoad(object value) {} }
    public class MonoBehaviour : Object {}
    public class GameObject : Object {
        public GameObject(string name) {}
        public T AddComponent<T>() where T:new() => new T();
        public static GameObject Find(string path) => new GameObject(path);
    }
    public static class Application { public static string persistentDataPath = "/fake-unity-root"; public static bool isBatchMode = true; }
    public static class SystemInfo { public static string graphicsDeviceType = "Null"; }
    public class Camera { public static event Action<Camera> onPostRender; public static void Render() => onPostRender?.Invoke(new Camera()); }
    public static class Time { public static float realtimeSinceStartup; public static float timeScale = 1; }
    public static class Debug { public static void LogWarning(string value) { Console.Error.WriteLine(value); } }
}
public static class Util { public static string Root; public static string RootFolder() => Root; }
public class Game { public static Game Instance; public bool Loading; public bool IsLoading()=>Loading; public bool IsInitialized()=>true; }
public class GameClock { public static GameClock Instance; public double Value; public double GetTime()=>Value; }
public class SpeedControlScreen {
    private int pauseCount=1;
    public static SpeedControlScreen Instance; public bool IsPaused { get=>pauseCount>0; set=>pauseCount=value?1:0; } public int Unpauses;
    public void Pause(bool playSound) { pauseCount++; }
    public void Unpause(bool playSound) { pauseCount=Math.Max(0,pauseCount-1); Unpauses++; }
}
public static class KCrashReporter { public static bool hasCrash; }
public static class SaveLoader { public static string Active; public static string GetActiveSaveFilePath()=>Active; }
public class ScreenPrefabs { public static ScreenPrefabs Instance=new ScreenPrefabs(); public object loadingOverlay = new object(); }
public class GameScreenManager { public static GameScreenManager Instance = new GameScreenManager(); }
public class Label { public string text {get;set;} }
public class ConfirmDialogScreen {
    public bool isActiveAndEnabled {get;set;} = true;
    private Label titleText = new Label {text="MOD DIFFERENCES"};
    private Label popupMessage = new Label {text="Save game mods differ from currently active mods: fixture"};
    public static int Confirmations;
    public void OnSelect_OK() {Confirmations++; isActiveAndEnabled=false; SpeedControlScreen.Instance.Unpause(false);}
}
public class KScreenManager {
    public static KScreenManager Instance = new KScreenManager();
    private object[] screenStack = {new ConfirmDialogScreen(), new ConfirmDialogScreen {isActiveAndEnabled=false}};
    public string DebugScreenStack()=>"fixture-native-screen-stack";
}
public static class LoadingOverlay { public static Action Pending; public static void Load(Action callback) { Pending=callback; } }
public static class LoadScreen { public static int Loads; public static void DoLoad(string path) { Loads++; SaveLoader.Active=path; Game.Instance=new Game(); } }
public static class App { public static bool QuitCalled; public static void Quit() { QuitCalled=true; } }
namespace OniHarness.Runtime { public static class Mod { public static string GameError; } }

public static class ContractMain {
    private static int checks;
    private static string root, nonce, save;
    private static OniHarness.Runtime.InstanceControl control;
    private static readonly JsonSerializerOptions json = new JsonSerializerOptions { PropertyNamingPolicy=JsonNamingPolicy.CamelCase };
    private static void Check(bool value, string label) { checks++; if (!value) throw new Exception(label); }
    private static void Invoke(string method, params object[] args) {
        try { typeof(OniHarness.Runtime.InstanceControl).GetMethod(method, BindingFlags.NonPublic|BindingFlags.Instance).Invoke(control,args); }
        catch(TargetInvocationException ex) { throw ex.InnerException; }
    }
    private static JsonElement Read(string file) => JsonDocument.Parse(File.ReadAllText(Path.Combine(root,file))).RootElement.Clone();
    private static void Poll() { UnityEngine.Time.realtimeSinceStartup+=1; Invoke("Update"); }
    private static JsonElement Send(string id, string action, double seconds=0, string path=null, string hash=null, string expectedTitle=null, string expectedMessage=null) {
        File.WriteAllText(Path.Combine(root,"instance-command.json"), JsonSerializer.Serialize(new {id, instance="guest-1", nonce, action, seconds, path, sha256=hash, expectedTitle, expectedMessage},json));
        Poll();
        return Read("instance-response.json");
    }
    private static void Failed(JsonElement response, string text) { Check(response.GetProperty("state").GetString()=="failed",text); }
    public static int Main() {
        root=Path.Combine(Path.GetTempPath(),"instance-contract-"+Guid.NewGuid().ToString("N"));
        Directory.CreateDirectory(root);
        try {
            OniHarness.Runtime.InstanceControl.Install(Path.Combine(root,"inactive-no-args"));
            Check(!Directory.Exists(Path.Combine(root,"inactive-no-args")),"ordinary launch without instance argument leaves controls inactive");
            var parseArgument=typeof(OniHarness.Runtime.InstanceControl).GetMethod("Argument",BindingFlags.NonPublic|BindingFlags.Static);
            foreach(var invalid in new[]{new[]{"--oniharness-instance"},new[]{"--oniharness-instance","one","--oniharness-instance","two"}}) {
                bool rejected=false;
                try {parseArgument.Invoke(null,new object[]{invalid,"--oniharness-instance"});} catch(TargetInvocationException ex) {rejected=ex.InnerException is InvalidDataException;}
                Check(rejected,"missing and duplicate instance arguments rejected");
            }
            string user=Path.Combine(root,"user"); Directory.CreateDirectory(user);
            string saves=Path.Combine(user,"save_files"); Directory.CreateDirectory(saves);
            save=Path.Combine(saves,"test-copy.sav"); File.WriteAllText(save,"private test copy only");
            string hash=Convert.ToHexString(SHA256.HashData(File.ReadAllBytes(save))).ToLowerInvariant();
            File.WriteAllText(Path.Combine(root,"instance-profile.json"),JsonSerializer.Serialize(new {identity="guest-1",userRoot=user,approvedSaves=new[]{new {path=save,sha256=hash}}},json));
            Util.Root=user;
            Game.Instance=new Game(); GameClock.Instance=new GameClock(); SpeedControlScreen.Instance=new SpeedControlScreen(); SaveLoader.Active=save;
            control=new OniHarness.Runtime.InstanceControl();
            Invoke("Initialize",root,"guest-1",user);
            var status=Read("instance-status.json"); nonce=status.GetProperty("runNonce").GetString();
            Check(status.GetProperty("rootVerified").GetBoolean(),"native root verified");
            Check(status.GetProperty("identity").GetString()=="guest-1","instance identity reported");
            Check(status.GetProperty("activeSavePath").GetString()==save,"exact native active save reported");
            var assemblies=Read("instance-assemblies.json");
            Check(status.GetProperty("assemblyReceipt").GetString()=="instance-assemblies.json" && assemblies.GetProperty("assemblies").GetArrayLength()>0,"assembly file receipts captured separately");
            Check(assemblies.GetProperty("runNonce").GetString()==nonce && assemblies.GetProperty("identity").GetString()=="guest-1", "assembly receipt bound to current instance run");
            bool actualAssemblyHash=false;
            foreach(var assembly in assemblies.GetProperty("assemblies").EnumerateArray()) {
                if(assembly.GetProperty("name").GetString()!="Contract") continue;
                string location=assembly.GetProperty("location").GetString();
                actualAssemblyHash=File.Exists(location) && assembly.GetProperty("sha256").GetString()==Convert.ToHexString(SHA256.HashData(File.ReadAllBytes(location))).ToLowerInvariant();
            }
            Check(actualAssemblyHash,"assembly receipt hashes actual loaded assembly file");
            Check(!status.TryGetProperty("assemblies",out _),"frequent status excludes assembly list");
            string receiptBefore=File.ReadAllText(Path.Combine(root,"instance-assemblies.json"));
            Poll();
            Check(File.ReadAllText(Path.Combine(root,"instance-assemblies.json"))==receiptBefore,"unchanged assembly list is not rewritten during status poll");
            Check(status.GetProperty("batchMode").GetBoolean() && status.GetProperty("graphicsDevice").GetString()=="Null", "native graphics mode status reported");
            UnityEngine.Camera.Render(); Poll();
            Check(Read("instance-status.json").GetProperty("cameraRenderCallbacks").GetInt32()==1,"camera callback counted without screenshot");
            var firstControl=control; control=new OniHarness.Runtime.InstanceControl();
            bool duplicateRootRejected=false;
            try { Invoke("Initialize",root,"guest-1",user); } catch(IOException) { duplicateRootRejected=true; }
            Check(duplicateRootRejected,"second companion cannot claim same control root"); control=firstControl;
            string invalidNonce=nonce; nonce="another-run";
            File.WriteAllText(Path.Combine(root,"instance-command.json"),JsonSerializer.Serialize(new {id="wrong-nonce",instance="guest-1",nonce,action="quit"},json)); Poll();
            Check(!App.QuitCalled,"wrong run nonce cannot quit"); nonce=invalidNonce;
            File.WriteAllText(Path.Combine(root,"instance-command.json"),JsonSerializer.Serialize(new {id="wrong-owner",instance="guest-2",nonce,action="quit"},json)); Poll();
            Check(!App.QuitCalled,"wrong instance cannot quit");
            File.WriteAllText(Path.Combine(root,"instance-command.json"),new string('x',16385)); Poll();
            Check(Read("instance-status.json").GetProperty("error").GetString().Contains("16384"),"oversized command bounded");
            Failed(Send("bad-duration","step",seconds:6),"step maximum guarded");
            Failed(Send("zero-duration","step",seconds:0),"step positive duration guarded");
            SpeedControlScreen.Instance.IsPaused=false;
            Failed(Send("not-paused","step",seconds:.001),"unpaused start rejected");
            Send("pause","pause"); Check(SpeedControlScreen.Instance.IsPaused,"pause works");
            string outside=Path.Combine(root,"personal.sav");File.WriteAllText(outside,"original");
            Failed(Send("outside-root","load",path:outside),"outside native root rejected");
            string unapproved=Path.Combine(saves,"unapproved.sav");File.WriteAllText(unapproved,"unapproved");
            Failed(Send("unapproved","load",path:unapproved),"unapproved save copy rejected");
            Failed(Send("bad-hash","load",path:save,hash:new string('0',64)),"command save hash checked");
            File.AppendAllText(save,"modified");
            Failed(Send("changed-copy","load",path:save),"profile save hash checked");
            File.WriteAllText(save,"private test copy only");
            File.Delete(save); File.CreateSymbolicLink(save,outside);
            Failed(Send("symlink-copy","load",path:save),"symbolic link cannot escape test save root");
            File.Delete(save);File.WriteAllText(save,"private test copy only");
            Util.Root=Path.Combine(root,"another-user");
            Failed(Send("wrong-root","load",path:save),"actual native root assertion enforced");
            Send("pause-wrong-root","pause"); Check(SpeedControlScreen.Instance.IsPaused,"cleanup pause allowed on root mismatch");
            Util.Root=user;
            Send("queued-hash-race","load",path:save);
            File.WriteAllText(save,"changed after asynchronous load acceptance");
            string profilePath=Path.Combine(root,"instance-profile.json"), cachedProfile=File.ReadAllText(profilePath);
            string changedHash=Convert.ToHexString(SHA256.HashData(File.ReadAllBytes(save))).ToLowerInvariant();
            File.WriteAllText(profilePath,cachedProfile.Replace(hash,changedHash));
            LoadingOverlay.Pending(); Poll();
            var hashRace=Read("instance-response.json");
            Failed(hashRace,"queued native load rechecks the original approved SHA256");
            Check(hashRace.GetProperty("id").GetString()=="queued-hash-race" && LoadScreen.Loads==0,"changed queued checkpoint never reaches native loader and fails matching request");
            Check(Read("instance-status.json").GetProperty("loadingSave").ValueKind==JsonValueKind.Null && SpeedControlScreen.Instance.IsPaused,"failed queued hash check clears pending load and preserves pause");
            File.WriteAllText(profilePath,cachedProfile);
            File.WriteAllText(save,"private test copy only");
            Send("queued-symlink-race","load",path:save,hash:hash);
            File.Delete(save); File.CreateSymbolicLink(save,outside);
            LoadingOverlay.Pending(); Poll();
            var symlinkRace=Read("instance-response.json");
            Failed(symlinkRace,"queued native load rechecks symbolic link boundaries");
            Check(symlinkRace.GetProperty("id").GetString()=="queued-symlink-race" && LoadScreen.Loads==0,"redirected queued checkpoint never reaches native loader and fails matching request");
            Check(Read("instance-status.json").GetProperty("loadingSave").ValueKind==JsonValueKind.Null && SpeedControlScreen.Instance.IsPaused,"failed queued boundary check clears pending load and preserves pause");
            File.Delete(save); File.WriteAllText(save,"private test copy only");
            var accepted=Send("load-copy","load",path:save,hash:hash);
            Check(accepted.GetProperty("state").GetString()=="accepted","native load asynchronous acceptance");
            Check(LoadScreen.Loads==0,"load callback deferred");
            Poll(); Check(Read("instance-status.json").GetProperty("loadingSave").GetString()==save,"old game does not clear load request prematurely");
            LoadingOverlay.Pending(); Poll();
            Check(LoadScreen.Loads==1,"native exact save load called once");
            Check(Read("instance-status.json").GetProperty("ownedSave").GetString()==save,"loaded native save verified as approved copy");
            Send("other-pause","pause"); Send("load-copy","load",path:save);
            Check(LoadScreen.Loads==1,"duplicate command id cannot load twice");
            var running=Send("step-good","step",seconds:.001);
            Check(running.GetProperty("state").GetString()=="running","step start response");
            Check(!SpeedControlScreen.Instance.IsPaused,"step releases one native pause");
            GameClock.Instance.Value+=.5; System.Threading.Thread.Sleep(8); Poll();
            var done=Read("instance-response.json");
            Check(done.GetProperty("id").GetString()=="step-good" && done.GetProperty("state").GetString()=="completed","watchdog completes matching command");
            Check(done.GetProperty("nativeSeconds").GetDouble()==.5,"native positive clock advancement recorded");
            Check(done.GetProperty("paused").GetBoolean() && SpeedControlScreen.Instance.IsPaused,"watchdog final native pause");
            Check(done.GetProperty("simulationSecondsBefore").GetDouble()==0 && done.GetProperty("simulationSecondsAfter").GetDouble()==.5,"step before/after native clock recorded");
            Check(!string.IsNullOrEmpty(done.GetProperty("startedAt").GetString())&&!string.IsNullOrEmpty(done.GetProperty("completedAt").GetString()),"step UTC interval recorded");
            int unpauses=SpeedControlScreen.Instance.Unpauses;
            Send("replay-interim","pause"); var replay=Send("step-good","step",seconds:.001);
            Check(replay.GetProperty("state").GetString()=="completed"&&SpeedControlScreen.Instance.Unpauses==unpauses,"completed step replay cannot unpause twice");
            Send("step-zero-progress","step",seconds:.001); System.Threading.Thread.Sleep(8); Poll();
            Failed(Read("instance-response.json"),"zero native progress fails");
            Check(SpeedControlScreen.Instance.IsPaused,"zero-progress step still pauses");
            SpeedControlScreen.Instance.IsPaused=true; SpeedControlScreen.Instance.Pause(false);
            unpauses=SpeedControlScreen.Instance.Unpauses;
            Send("step-nested-pause","step",seconds:.001); System.Threading.Thread.Sleep(8); Poll();
            var nested=Read("instance-response.json");
            Failed(nested,"remaining native pause keeps zero-progress failure");
            Check(nested.GetProperty("pauseCountBeforeRelease").GetInt32()==2 && nested.GetProperty("pauseCountAfterRelease").GetInt32()==1,"diagnostics retain exact native pause counts");
            Check(nested.GetProperty("screenStackBeforeRelease").GetString()=="fixture-native-screen-stack","step preserves the pre-release native screen stack");
            Check(SpeedControlScreen.Instance.Unpauses==unpauses+1 && SpeedControlScreen.Instance.IsPaused,"diagnostics never release an unexplained second hold");
            UnityEngine.Time.timeScale=.5f; Poll();
            Check(Read("instance-status.json").GetProperty("timeScale").GetDouble()==.5,"status observes time scale without changing it");
            var dialogs=Read("instance-status.json").GetProperty("confirmDialogs");
            Check(dialogs.GetArrayLength()==1,"dialog diagnostics exclude inactive screens");
            Check(dialogs[0].GetProperty("title").GetString()=="MOD DIFFERENCES" && dialogs[0].GetProperty("message").GetString()=="Save game mods differ from currently active mods: fixture","dialog diagnostics retain visible title and message");
            Failed(Send("ack-wrong-title","acknowledge-mod-differences",expectedTitle:"ERROR",expectedMessage:"unknown"),"error dialogs cannot be acknowledged");
            Failed(Send("ack-stale-message","acknowledge-mod-differences",expectedTitle:"MOD DIFFERENCES",expectedMessage:"Save game mods differ from currently active mods: stale"),"changed dialog cannot be acknowledged");
            Check(ConfirmDialogScreen.Confirmations==0,"failed acknowledgments invoke no native handler");
            var ack=Send("ack-reviewed","acknowledge-mod-differences",expectedTitle:"MOD DIFFERENCES",expectedMessage:"Save game mods differ from currently active mods: fixture");
            Check(ack.GetProperty("state").GetString()=="completed" && ConfirmDialogScreen.Confirmations==1 && SpeedControlScreen.Instance.IsPaused,"reviewed acknowledgment uses native handler and leaves game paused");
            Send("ack-reviewed","acknowledge-mod-differences",expectedTitle:"MOD DIFFERENCES",expectedMessage:"Save game mods differ from currently active mods: fixture");
            Check(ConfirmDialogScreen.Confirmations==1,"acknowledgment replay cannot invoke twice");
            SpeedControlScreen.Instance.IsPaused=true;
            Send("step-changed-game","step",seconds:1); Game.Instance=new Game(); Poll();
            Failed(Read("instance-response.json"),"world change aborts active step");
            Check(SpeedControlScreen.Instance.IsPaused,"world change pauses current screen");
            Send("step-destroy","step",seconds:1); Invoke("OnDestroy");
            Check(SpeedControlScreen.Instance.IsPaused,"destroy watchdog pauses active step");
            UnityEngine.Camera.Render(); Poll();
            Check(Read("instance-status.json").GetProperty("cameraRenderCallbacks").GetInt32()==1,"camera callback unbound on destroy");
            Util.Root=Path.Combine(root,"bad-root-at-quit"); Send("quit-cleanup","quit");
            Check(App.QuitCalled,"normal native quit available for isolation failure cleanup");
            Invoke("OnDestroy");
            Util.Root=user; control=new OniHarness.Runtime.InstanceControl(); Invoke("Initialize",root,"guest-1",user);
            Check(Read("instance-status.json").GetProperty("runNonce").GetString()!=nonce,"shutdown releases control root for a fresh isolated run nonce");
            Invoke("OnDestroy");
            Console.WriteLine("PASS "+checks+" production instance-control contract assertions (game-free doubles)");
            return 0;
        } finally { Directory.Delete(root,true); }
    }
}
'''


class InstanceContractTests(unittest.TestCase):
    @unittest.skipUnless(shutil.which("dotnet"), "dotnet SDK is required")
    def test_production_companion_contract(self):
        with tempfile.TemporaryDirectory(prefix="oniharness-instance-contract-") as temporary:
            directory = pathlib.Path(temporary)
            shutil.copyfile(SOURCE, directory / "InstanceControl.cs")
            (directory / "Doubles.cs").write_text(DOUBLES)
            (directory / "Contract.csproj").write_text(
                '<Project Sdk="Microsoft.NET.Sdk"><PropertyGroup>'
                '<OutputType>Exe</OutputType><TargetFramework>net10.0</TargetFramework>'
                '<Nullable>disable</Nullable><ImplicitUsings>disable</ImplicitUsings>'
                '<NoWarn>SYSLIB0012</NoWarn>'
                '</PropertyGroup></Project>'
            )
            result = subprocess.run(
                ["dotnet", "run", "--project", str(directory / "Contract.csproj"), "-c", "Release"],
                capture_output=True, text=True, timeout=120, check=False,
            )
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertIn("production instance-control contract assertions", result.stdout)
            print(result.stdout.strip())


if __name__ == "__main__":
    unittest.main()
