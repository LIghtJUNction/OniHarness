using System;
using System.Collections.Generic;
using System.IO;
using System.Reflection;
using System.Security.Cryptography;
using System.Text;
using System.Threading;
using Newtonsoft.Json;
using UnityEngine;

namespace OniHarness.Runtime
{
    // Optional, instance-scoped control. It never changes early user-directory or Steam initialization.
    public sealed class InstanceControl : MonoBehaviour
    {
        private const int MaxCommandBytes = 16384;
        private string root, identity, nonce, expectedUserRoot, error, ownedSave, loadingSave;
        private string activeCommandId;
        private int pid, assembliesDirty = 1;
        private int cameraRenderCallbacks;
        private bool headlessRequested;
        private float nextPoll;
        private Profile profile;
        private FileStream rootLease;
        private Command runningStep;
        private DateTimeOffset stepDeadline;
        private double stepStart;
        private Game stepGame;
        private Game gameBeforeLoad;
        private bool loadDispatched;
        private string stepStartedAt;
        private SpeedControlScreen stepScreen;
        private int? stepPauseCountBeforeRelease, stepPauseCountAfterRelease;
        private string stepScreenStack;
        private static readonly FieldInfo pauseCountField = typeof(SpeedControlScreen).GetField("pauseCount", BindingFlags.Instance | BindingFlags.NonPublic);
        private readonly Dictionary<string, object> assemblyReceipts = new Dictionary<string, object>(StringComparer.Ordinal);
        private readonly Dictionary<string, Response> responses = new Dictionary<string, Response>(StringComparer.Ordinal);
        private Response lastResponse;

        public static void Install(string directory)
        {
            string[] args = Environment.GetCommandLineArgs();
            string id = Argument(args, "--oniharness-instance");
            if (id == null) return;
            if (id.Length < 1 || id.Length > 80 || Array.Exists(id.ToCharArray(), c => !char.IsLetterOrDigit(c) && c != '-' && c != '_'))
                throw new InvalidDataException("Instance identity must contain 1..80 letters, digits, '-' or '_'.");
            string control = Argument(args, "--oniharness-control");
            if (control == null || !SamePath(control, directory))
                throw new InvalidDataException("Instance control root does not match its explicit launch argument.");
            var owner = new GameObject("OniHarness.InstanceControl." + id);
            DontDestroyOnLoad(owner);
            owner.AddComponent<InstanceControl>().Initialize(directory, id, Argument(args, "--oniharness-user-root"));
        }

        private static string Argument(string[] args, string name)
        {
            int i = Array.IndexOf(args, name);
            if (i < 0) return null;
            if (i + 1 >= args.Length || string.IsNullOrWhiteSpace(args[i + 1]) || args[i + 1].StartsWith("--", StringComparison.Ordinal))
                throw new InvalidDataException("Missing argument for " + name);
            if (Array.LastIndexOf(args, name) != i) throw new InvalidDataException("Duplicate " + name);
            return args[i + 1];
        }

        private void Initialize(string directory, string id, string assertedRoot)
        {
            root = Path.GetFullPath(directory);
            identity = id;
            nonce = Guid.NewGuid().ToString("N");
            expectedUserRoot = assertedRoot == null ? null : Path.GetFullPath(assertedRoot);
            using (var process = System.Diagnostics.Process.GetCurrentProcess()) pid = process.Id;
            rootLease = new FileStream(Path.Combine(root, "instance-owner.lock"), FileMode.OpenOrCreate, FileAccess.ReadWrite, FileShare.None);
            headlessRequested = Array.IndexOf(Environment.GetCommandLineArgs(), "--oniharness-headless") >= 0;
            try
            {
                string path = Path.Combine(root, "instance-profile.json");
                profile = JsonConvert.DeserializeObject<Profile>(ReadBounded(path, 131072));
                ValidateProfile();
            }
            catch (Exception ex) { error = "Instance profile: " + ex.Message; }
            AppDomain.CurrentDomain.AssemblyLoad += AssemblyLoaded;
            Camera.onPostRender += CameraRendered;
            WriteStatus();
        }

        private void ValidateProfile()
        {
            if (profile == null || profile.Identity != identity || string.IsNullOrWhiteSpace(profile.UserRoot))
                throw new InvalidDataException("Profile identity and userRoot are required.");
            if (!SamePath(profile.UserRoot, Util.RootFolder()))
                throw new InvalidDataException("Native user root differs from instance profile; isolation is not established.");
            if (expectedUserRoot != null && !SamePath(profile.UserRoot, expectedUserRoot))
                throw new InvalidDataException("Native user root differs from --oniharness-user-root assertion.");
            if (profile.ApprovedSaves == null || profile.ApprovedSaves.Length > 64)
                throw new InvalidDataException("Profile approvedSaves must be an array with at most 64 entries.");
        }

        private void AssemblyLoaded(object sender, AssemblyLoadEventArgs args) => Interlocked.Exchange(ref assembliesDirty, 1);
        private void CameraRendered(Camera camera) => cameraRenderCallbacks++;

        private void Update()
        {
            try
            {
                if (runningStep != null)
                {
                    bool changed = !ReferenceEquals(Game.Instance, stepGame) || !ReferenceEquals(SpeedControlScreen.Instance, stepScreen);
                    if (changed || KCrashReporter.hasCrash || Mod.GameError != null || DateTimeOffset.UtcNow >= stepDeadline)
                        FinishStep(changed ? "Loaded game changed during step." : Mod.GameError ?? (KCrashReporter.hasCrash ? "Native game reported a crash." : null));
                }
                if (loadingSave != null && loadDispatched && IsLoaded() &&
                    (!ReferenceEquals(Game.Instance, gameBeforeLoad) || SamePath(SaveLoader.GetActiveSaveFilePath(), loadingSave)))
                {
                    Pause();
                    string actual = SaveLoader.GetActiveSaveFilePath();
                    if (SamePath(actual, loadingSave)) ownedSave = actual;
                    else error = "Native loader completed with a different save path.";
                    loadingSave = null;
                }
                if (Time.realtimeSinceStartup < nextPoll) return;
                nextPoll = Time.realtimeSinceStartup + 0.2f;
                PollCommand();
                WriteStatus();
            }
            catch (Exception ex)
            {
                error = ex.Message;
                if (runningStep != null) FinishStep(error);
                try { WriteStatus(); } catch (Exception write) { Debug.LogWarning("[OniHarness] Instance status failed: " + write.Message); }
            }
        }

        private void PollCommand()
        {
            string path = Path.Combine(root, "instance-command.json");
            if (!File.Exists(path)) return;
            Command command;
            try { command = JsonConvert.DeserializeObject<Command>(ReadBounded(path, MaxCommandBytes)); }
            catch (Exception ex) when (ex is JsonException || ex is InvalidDataException || ex is DecoderFallbackException)
            { error = "Invalid instance command: " + ex.Message; return; }
            if (command == null || string.IsNullOrWhiteSpace(command.Id) || command.Id.Length > 128) { error = "Instance command needs an id of 1..128 characters."; return; }
            if (command.Instance != identity || command.Nonce != nonce)
            {
                error = "Rejected command with another instance identity or run nonce.";
                return;
            }
            if (command.Id == activeCommandId) return;
            if (responses.TryGetValue(command.Id, out Response replay)) { Publish(replay, remember: false); activeCommandId = command.Id; return; }
            if (responses.Count >= 4096 && command.Action != "quit" && command.Action != "pause")
            {
                Publish(Result(command, "failed", "Per-run command journal is full; restart this owned instance before further actions."), remember: false);
                activeCommandId = command.Id;
                return;
            }
            activeCommandId = command.Id;
            try
            {
                error = null;
                if (runningStep != null && command.Action != "pause" && command.Action != "quit")
                    throw new InvalidOperationException("A bounded step is already running.");
                switch (command.Action)
                {
                    case "pause":
                        if (runningStep != null) FinishStep("Step interrupted by pause command.");
                        Pause();
                        Publish(Result(command, "completed"));
                        break;
                    case "step":
                        ValidateProfile();
                        if (!IsLoaded() || SpeedControlScreen.Instance == null || !SpeedControlScreen.Instance.IsPaused)
                            throw new InvalidOperationException("Step requires a loaded, already paused native game.");
                        if (double.IsNaN(command.Seconds) || double.IsInfinity(command.Seconds) || command.Seconds <= 0 || command.Seconds > 5)
                            throw new InvalidDataException("Step seconds must be >0 and <=5.");
                        RequireOwnedSave();
                        stepGame = Game.Instance;
                        stepScreen = SpeedControlScreen.Instance;
                        stepPauseCountBeforeRelease = ReadNativePauseCount();
                        stepScreenStack = KScreenManager.Instance == null ? null : KScreenManager.Instance.DebugScreenStack();
                        stepStart = GameClock.Instance.GetTime();
                        stepStartedAt = System.DateTime.UtcNow.ToString("o");
                        stepDeadline = DateTimeOffset.UtcNow.AddSeconds(command.Seconds);
                        runningStep = command;
                        // A single release preserves unexplained native/UI pause holds. Zero progress is a failed step.
                        stepScreen.Unpause(playSound: false);
                        stepPauseCountAfterRelease = ReadNativePauseCount();
                        Publish(Result(command, "running"));
                        break;
                    case "load":
                        ValidateProfile();
                        if (loadingSave != null || (Game.Instance != null && Game.Instance.IsLoading())) throw new InvalidOperationException("A native load is already pending.");
                        string approved = ApproveSave(command.Path, command.Sha256);
                        if (ScreenPrefabs.Instance == null || ScreenPrefabs.Instance.loadingOverlay == null ||
                            (GameScreenManager.Instance == null && GameObject.Find("/SceneInitializerFE/FrontEndManager") == null))
                            throw new InvalidOperationException("Native loading screen is not ready.");
                        Pause();
                        ownedSave = null;
                        loadingSave = approved;
                        gameBeforeLoad = Game.Instance;
                        loadDispatched = false;
                        Publish(Result(command, "accepted"));
                        LoadingOverlay.Load(() =>
                        {
                            try
                            {
                                // The overlay queues this callback. Revalidate the original cached
                                // approval at the actual native-load boundary, without rereading or
                                // expanding the allowlist while the request is pending.
                                ValidateProfile();
                                ApproveSave(approved, command.Sha256);
                                loadDispatched = true;
                                LoadScreen.DoLoad(approved);
                            }
                            catch (Exception ex)
                            {
                                loadingSave = null;
                                loadDispatched = false;
                                error = ex.Message;
                                Pause();
                                Publish(Result(command, "failed", ex.Message));
                            }
                        });
                        break;
                    case "acknowledge-mod-differences":
                        ValidateProfile();
                        if (!IsLoaded() || SpeedControlScreen.Instance == null || !SpeedControlScreen.Instance.IsPaused)
                            throw new InvalidOperationException("Acknowledgment requires a loaded paused native game.");
                        RequireOwnedSave();
                        AcknowledgeModDifferences(command.ExpectedTitle, command.ExpectedMessage);
                        Pause();
                        Publish(Result(command, "completed"));
                        break;
                    case "quit":
                        if (runningStep != null) FinishStep("Step interrupted by quit command.");
                        Pause();
                        Publish(Result(command, "accepted"));
                        WriteStatus();
                        App.Quit();
                        break;
                    default: throw new InvalidDataException("Allowed instance actions: pause, step, load, acknowledge-mod-differences, quit.");
                }
            }
            catch (Exception ex) { Publish(Result(command, "failed", ex.Message)); }
        }

        private static bool IsLoaded() => Game.Instance != null && !Game.Instance.IsLoading() && Game.Instance.IsInitialized() && GameClock.Instance != null;

        private void RequireOwnedSave()
        {
            string actual = SaveLoader.GetActiveSaveFilePath();
            if (ownedSave != null && SamePath(ownedSave, actual)) return;
            ownedSave = ApproveSave(actual, null);
        }

        private string ApproveSave(string path, string requestedHash)
        {
            if (string.IsNullOrWhiteSpace(path) || !Path.IsPathRooted(path)) throw new InvalidDataException("An absolute approved save-copy path is required.");
            string full = Path.GetFullPath(path);
            string boundary = Path.GetFullPath(profile.UserRoot).TrimEnd(Path.DirectorySeparatorChar) + Path.DirectorySeparatorChar;
            if (!full.StartsWith(boundary, PathComparison) || !full.EndsWith(".sav", StringComparison.OrdinalIgnoreCase) || !File.Exists(full))
                throw new InvalidDataException("Save must be an existing .sav inside this instance's native user root.");
            RejectLinks(full);
            SaveApproval approval = Array.Find(profile.ApprovedSaves, save => save != null && SamePath(save.Path, full));
            if (approval == null || approval.Sha256 == null || approval.Sha256.Length != 64)
                throw new InvalidDataException("Save path is not an explicitly approved test copy.");
            string hash = Hash(full);
            if (!string.Equals(hash, approval.Sha256, StringComparison.OrdinalIgnoreCase) ||
                (requestedHash != null && !string.Equals(hash, requestedHash, StringComparison.OrdinalIgnoreCase)))
                throw new InvalidDataException("Approved save-copy SHA256 does not match.");
            return full;
        }

        private static void RejectLinks(string path)
        {
            for (string current = path; !string.IsNullOrEmpty(current); current = Path.GetDirectoryName(current))
                if ((File.GetAttributes(current) & FileAttributes.ReparsePoint) != 0)
                    throw new InvalidDataException("Instance save paths cannot traverse symbolic links/reparse points.");
        }

        private static StringComparison PathComparison => Path.DirectorySeparatorChar == '\\' ? StringComparison.OrdinalIgnoreCase : StringComparison.Ordinal;
        private static bool SamePath(string a, string b) => !string.IsNullOrWhiteSpace(a) && !string.IsNullOrWhiteSpace(b) &&
            string.Equals(Path.GetFullPath(a).TrimEnd(Path.DirectorySeparatorChar), Path.GetFullPath(b).TrimEnd(Path.DirectorySeparatorChar), PathComparison);
        private static string Hash(string path)
        {
            using (var stream = File.OpenRead(path))
            using (var sha = SHA256.Create()) return BitConverter.ToString(sha.ComputeHash(stream)).Replace("-", "").ToLowerInvariant();
        }

        private static string ReadBounded(string path, int maximum)
        {
            using (var stream = File.OpenRead(path))
            {
                var buffer = new byte[maximum + 1];
                int count = 0, read;
                while (count < buffer.Length && (read = stream.Read(buffer, count, buffer.Length - count)) > 0) count += read;
                if (count < 2 || count > maximum) throw new InvalidDataException("Control file must contain 2.." + maximum + " bytes.");
                return new UTF8Encoding(false, true).GetString(buffer, 0, count).TrimStart('\uFEFF');
            }
        }

        private static void Pause()
        {
            var screen = SpeedControlScreen.Instance;
            if (screen != null && !screen.IsPaused) screen.Pause(playSound: false);
        }

        private static int? ReadNativePauseCount()
        {
            var screen = SpeedControlScreen.Instance;
            if (screen == null || pauseCountField == null) return null;
            try { return pauseCountField.GetValue(screen) is int count ? (int?)count : null; }
            catch { return null; } // Diagnostics never alter native pause ownership.
        }

        private static object[] ReadConfirmDialogs()
        {
            var result = new List<object>();
            try
            {
                var manager = KScreenManager.Instance;
                var stack = typeof(KScreenManager).GetField("screenStack", BindingFlags.Instance | BindingFlags.NonPublic)?.GetValue(manager) as System.Collections.IEnumerable;
                if (stack == null) return result.ToArray();
                foreach (object screen in stack)
                {
                    if (screen == null || screen.GetType().Name != "ConfirmDialogScreen") continue;
                    var type = screen.GetType();
                    var active = type.GetProperty("isActiveAndEnabled")?.GetValue(screen, null);
                    if (!(active is bool enabled) || !enabled) continue;
                    Func<string, string> read = name =>
                    {
                        object label = type.GetField(name, BindingFlags.Instance | BindingFlags.NonPublic)?.GetValue(screen);
                        string value = label?.GetType().GetProperty("text")?.GetValue(label, null) as string;
                        return value == null ? null : value.Substring(0, Math.Min(value.Length, 4096));
                    };
                    result.Add(new { title = read("titleText"), message = read("popupMessage") });
                }
            }
            catch { } // Optional read-only UI diagnostics cannot affect native control.
            return result.ToArray();
        }

        private static void AcknowledgeModDifferences(string expectedTitle, string expectedMessage)
        {
            if (expectedTitle != "MOD DIFFERENCES" || expectedMessage == null ||
                !expectedMessage.StartsWith("Save game mods differ from currently active mods:", StringComparison.Ordinal) || expectedMessage.Length > 4096)
                throw new InvalidDataException("Explicit observed mod-differences title and message are required.");
            var manager = KScreenManager.Instance;
            var stack = typeof(KScreenManager).GetField("screenStack", BindingFlags.Instance | BindingFlags.NonPublic)?.GetValue(manager) as System.Collections.IEnumerable;
            object matched = null;
            int activeCount = 0;
            if (stack != null) foreach (object screen in stack)
            {
                if (screen == null || screen.GetType().Name != "ConfirmDialogScreen") continue;
                var type = screen.GetType();
                if (!(type.GetProperty("isActiveAndEnabled")?.GetValue(screen, null) is bool active) || !active) continue;
                activeCount++;
                Func<string, string> read = name =>
                {
                    object label = type.GetField(name, BindingFlags.Instance | BindingFlags.NonPublic)?.GetValue(screen);
                    return label?.GetType().GetProperty("text")?.GetValue(label, null) as string;
                };
                if (read("titleText") == expectedTitle && read("popupMessage") == expectedMessage) matched = screen;
            }
            if (activeCount != 1 || matched == null) throw new InvalidOperationException("Observed native confirmation changed or is ambiguous.");
            var confirm = matched.GetType().GetMethod("OnSelect_OK", BindingFlags.Instance | BindingFlags.Public, null, Type.EmptyTypes, null);
            if (confirm == null) throw new InvalidOperationException("Native confirmation handler is unavailable.");
            confirm.Invoke(matched, null);
        }

        private void FinishStep(string failure)
        {
            var command = runningStep;
            if (command == null) return;
            runningStep = null;
            Pause();
            double? elapsed = GameClock.Instance == null || !ReferenceEquals(Game.Instance, stepGame) ? (double?)null : GameClock.Instance.GetTime() - stepStart;
            if (failure == null && (!elapsed.HasValue || elapsed.Value <= 0)) failure = "No positive native-clock progress during bounded step; inspect remaining native pause holds.";
            var result = Result(command, failure == null ? "completed" : "failed", failure);
            result.NativeSeconds = elapsed;
            result.StartedAt = stepStartedAt;
            result.CompletedAt = System.DateTime.UtcNow.ToString("o");
            result.SimulationSecondsBefore = stepStart;
            result.SimulationSecondsAfter = elapsed.HasValue ? stepStart + elapsed.Value : (double?)null;
            result.Paused = SpeedControlScreen.Instance != null && SpeedControlScreen.Instance.IsPaused;
            result.PauseCountBeforeRelease = stepPauseCountBeforeRelease;
            result.PauseCountAfterRelease = stepPauseCountAfterRelease;
            result.ScreenStackBeforeRelease = stepScreenStack;
            Publish(result);
        }

        private Response Result(Command command, string state, string failure = null) => new Response
        {
            Id = command.Id, Action = command.Action, State = state, Error = failure,
            Instance = identity, Nonce = nonce, Pid = pid, CapturedAt = System.DateTime.UtcNow.ToString("o"),
            StartedAt = System.DateTime.UtcNow.ToString("o"), CompletedAt = state == "running" || state == "accepted" ? null : System.DateTime.UtcNow.ToString("o")
        };

        private void Publish(Response response, bool remember = true)
        {
            lastResponse = response;
            if (remember && (responses.ContainsKey(response.Id) || responses.Count < 4096))
            {
                responses[response.Id] = response;
            }
            Atomic("instance-response.json", response);
        }

        private void WriteStatus()
        {
            if (Interlocked.Exchange(ref assembliesDirty, 0) != 0)
            {
                bool changed = false;
                foreach (Assembly assembly in AppDomain.CurrentDomain.GetAssemblies())
                {
                    if (assembly.IsDynamic) continue;
                    string key = assembly.FullName;
                    if (assemblyReceipts.ContainsKey(key)) continue;
                    string location = null, source = null, hash = null, hashError = null;
                    try
                    {
                        location = assembly.Location;
                        source = assembly.CodeBase;
                        if (!string.IsNullOrEmpty(location) && File.Exists(location)) hash = Hash(location);
                        else hashError = "Assembly has no readable file location.";
                    }
                    catch (Exception ex) { hashError = ex.Message; }
                    assemblyReceipts[key] = new { name = assembly.GetName().Name, fullName = key, location, codeBase = source, sha256 = hash, error = hashError };
                    changed = true;
                }
                if (changed)
                    Atomic("instance-assemblies.json", new
                    {
                        schema = 1, capturedAt = System.DateTime.UtcNow.ToString("o"), pid, identity,
                        runNonce = nonce, assemblies = assemblyReceipts.Values
                    });
            }
            string actualRoot = null;
            try { actualRoot = Util.RootFolder(); } catch (Exception ex) { error = ex.Message; }
            Atomic("instance-status.json", new
            {
                schema = 1, capturedAt = System.DateTime.UtcNow.ToString("o"), pid, identity, runNonce = nonce, controlRoot = root,
                gameHasCrash = KCrashReporter.hasCrash, gameError = Mod.GameError,
                persistentDataPath = Application.persistentDataPath, nativeUserRoot = actualRoot, expectedUserRoot,
                batchMode = Application.isBatchMode, graphicsDevice = SystemInfo.graphicsDeviceType.ToString(), headlessRequested, cameraRenderCallbacks,
                rootVerified = profile != null && profile.Identity == identity && SamePath(profile.UserRoot, actualRoot) &&
                    (expectedUserRoot == null || SamePath(expectedUserRoot, actualRoot)),
                gameLoaded = IsLoaded(), loadingSave, activeSavePath = SaveLoader.GetActiveSaveFilePath(), ownedSave,
                paused = SpeedControlScreen.Instance == null ? (bool?)null : SpeedControlScreen.Instance.IsPaused,
                nativePauseCount = ReadNativePauseCount(), timeScale = Time.timeScale,
                confirmDialogs = ReadConfirmDialogs(),
                simulationSeconds = GameClock.Instance == null ? (double?)null : GameClock.Instance.GetTime(),
                stepId = runningStep?.Id, stepDeadlineUtc = runningStep == null ? (DateTimeOffset?)null : stepDeadline,
                lastResponse, assemblyReceipt = "instance-assemblies.json", assemblyCount = assemblyReceipts.Count, error
            });
        }

        private void Atomic(string filename, object value)
        {
            string destination = Path.Combine(root, filename), temporary = destination + "." + pid + ".tmp";
            File.WriteAllText(temporary, JsonConvert.SerializeObject(value));
            if (File.Exists(destination)) File.Replace(temporary, destination, null);
            else File.Move(temporary, destination);
        }

        private void OnDestroy()
        {
            AppDomain.CurrentDomain.AssemblyLoad -= AssemblyLoaded;
            Camera.onPostRender -= CameraRendered;
            try
            {
                if (runningStep != null) FinishStep("Instance companion destroyed during step.");
                if (loadingSave != null) Pause();
            }
            finally { rootLease?.Dispose(); }
        }

        private sealed class Profile
        {
            public string Identity { get; set; }
            public string UserRoot { get; set; }
            public SaveApproval[] ApprovedSaves { get; set; }
        }
        private sealed class SaveApproval { public string Path { get; set; } public string Sha256 { get; set; } }
        private sealed class Command
        {
            public string Id { get; set; }
            public string Instance { get; set; }
            public string Nonce { get; set; }
            public string Action { get; set; }
            public double Seconds { get; set; }
            public string Path { get; set; }
            public string Sha256 { get; set; }
            public string ExpectedTitle { get; set; }
            public string ExpectedMessage { get; set; }
        }
        private sealed class Response
        {
            [JsonProperty("id")] public string Id { get; set; }
            [JsonProperty("action")] public string Action { get; set; }
            [JsonProperty("state")] public string State { get; set; }
            [JsonProperty("error")] public string Error { get; set; }
            [JsonProperty("instance")] public string Instance { get; set; }
            [JsonProperty("nonce")] public string Nonce { get; set; }
            [JsonProperty("pid")] public int Pid { get; set; }
            [JsonProperty("capturedAt")] public string CapturedAt { get; set; }
            [JsonProperty("nativeSeconds")] public double? NativeSeconds { get; set; }
            [JsonProperty("paused")] public bool? Paused { get; set; }
            [JsonProperty("pauseCountBeforeRelease")] public int? PauseCountBeforeRelease { get; set; }
            [JsonProperty("pauseCountAfterRelease")] public int? PauseCountAfterRelease { get; set; }
            [JsonProperty("screenStackBeforeRelease")] public string ScreenStackBeforeRelease { get; set; }
            [JsonProperty("startedAt")] public string StartedAt { get; set; }
            [JsonProperty("completedAt")] public string CompletedAt { get; set; }
            [JsonProperty("simulationSecondsBefore")] public double? SimulationSecondsBefore { get; set; }
            [JsonProperty("simulationSecondsAfter")] public double? SimulationSecondsAfter { get; set; }
        }
    }
}
