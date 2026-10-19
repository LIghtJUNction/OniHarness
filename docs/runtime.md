# Headless gameplay and optional AI text view

This source-checkout addition runs the real ONI game and Unity through Steam,
without a game window or graphics device by default. A game process is required
for native colony logic; this is distinct from the game-free fixture host.
The original game-free/native tests remain available and never launch the game.
It is not included in the original v0.1.0 packages. Linux runtime support is
experimental; a successful startup does not certify every game/mod feature.

[Verified headless run](verification/headless.json) records a loaded three-duplicant
test save, positive native-clock progress and duplicant movement, zero camera
render callbacks, no OS game window or DRM descriptor, and client-loss stop/recovery.
It is one build/save/mod combination, not complete feature coverage or a measured
whole-game speedup.

[Earlier reduced-render verification](verification/runtime.json) includes a real save
copy, native clock/duplicant movement, a killed-client stop/recovery test, the
browser's exact-map check and a short paused ABBA rendering comparison. Those
observations apply to that installation/workload, not every colony or mod.

## Rendering and observation are separate options

| Option | What it does |
| --- | --- |
| `launch` / `launch --graphics headless` | Default: `-batchmode -nographics`; requires batch mode, a Null graphics device, zero camera render callbacks and no native crash |
| `launch --graphics none` | Compatibility alias for `headless` |
| `launch --graphics normal` | Explicit graphical client, original render interval; useful as a baseline |
| `launch --graphics reduced --interval 60 --update-rate 30` | Optional companion asks Unity to draw every 60 update frames and caps background updates at 30/sec |
| `observe --view none` | Record paused game data without terminal map output; default |
| `observe --view text` | Also print the exact map returned to the AI |
| `serve` | Optional read-only browser text spectator of recorded observations |

Headless mode uses the [Unity Player command-line options](https://docs.unity3d.com/6000.3/Documentation/Manual/PlayerCommandLineArguments.html).
It is not a hidden/minimized window, an Xvfb display or a reduced-render client.
The optional text spectator is a separate HTTP page; opening it does not enable
game graphics. Assets and game-side animation bookkeeping can still load/run;
memory savings and compatibility of visual mods are not guaranteed. Steam must
still be reachable; a desktop-free Steam/server deployment is not verified.

The headless companion skips presentation initializers: the gamepad cursor
render overlay (which tries to allocate a zero-size render target in batch mode),
menu video creation, the menu intro and the liquid renderer's multi-target
command buffer (unsupported by the Null device). These entrypoints require validation
after a game update. UI video-player children are deactivated before native
cloning as well, since in-game scheduled UI also creates a video panel.
Game simulation, AI, chores and physics are not patched.

The companion records native error dialogs in private `failure.json`, including
their actual message and stack trace. Native error handling is preserved; a
headless request does not authorize suppressing crashes or dismissing errors.

Reduced rendering is a separate **graphical** option using [Unity OnDemandRendering](https://docs.unity3d.com/6000.3/Documentation/ScriptReference/Rendering.OnDemandRendering.html).
It does not replace or patch simulation, AI, chores, building lifecycle, save
loading, physics or game speed. It retains the graphics device and game assets.
Reduced mode disables VSync and explicitly caps the update loop (30/sec by
default); otherwise skipped drawing can make that loop spin faster and increase
CPU use. This changes frame cadence, so verify simulation-clock progress and
frame-dependent mods. Normal mode restores the original VSync, target rate and
render interval. `--update-rate` accepts 15..240; it is not a game-speed setting.
Game-specific render work may still run outside Unity's skipped drawing passes;
measure CPU/GPU work and simulation progress rather than assuming a speedup.
Screenshots, animation timing, render callbacks and visual mods require their
own compatibility tests. Memory savings are not guaranteed.

## Install the optional companion

Use your own installed game, an existing Steam login and enabled OniMcp.
Exit ONI before installing. This installs only the companion's own DLL and
metadata under `mods/Local`, backs up `mods.json`,
and enables only the companion entry. New manifest backups go to
`~/Backups/OniHarness/runtime/YYYY-MM-DD` (`--backup-dir` overrides it).
Other mod entries are preserved. No
game/Unity DLLs are copied. The companion changes nothing during ordinary
launches without `--oniharness-control PATH`.

```bash
python3 scripts/install_runtime.py --game-path /path/to/OxygenNotIncluded \
  --mods-dir "/path/to/ONI-user-data/mods"
python3 scripts/runtime.py launch
python3 scripts/runtime.py status
```

The installer defaults to `EXPANSION1_ID`; pass `--dlc ''` for base game.
Steam must be reachable in the current desktop session. No direct executable
fallback, automatic restart or SteamCMD exists. Launch refuses an existing ONI
process. Launch waits up to 120 seconds for a fresh, live companion status
(`--timeout 1..300` changes this). A headless launch fails unless telemetry
confirms batch mode, Null graphics and zero camera renders. There is no automatic
graphical fallback or restart. `ready.json` proves engine readiness, not a loaded
colony; check OniMcp's launch state before testing. File control is local and
single-controller; use a distinct `--output` directory per session. The default
is `artifacts/runtime`. All later commands must use the same output directory.

For an installed mod's explicit diagnostic option, use repeatable long arguments,
for example `launch --game-arg=--cycletrim-perf-probe`. Short Steam/Unity options
and reserved `--oniharness-*` controls cannot be overridden this way. This does
not install CycleTrim or enable its probe during ordinary launches.

Only in an explicitly graphical session, switch render policy in the same process:

```bash
python3 scripts/runtime.py render normal
python3 scripts/runtime.py status
python3 scripts/runtime.py render reduced --interval 60
python3 scripts/runtime.py status
```

Headless sessions cannot switch to graphical rendering without an explicit exit
and relaunch. Do not reuse an output directory with an old run lease. Archive
the completed lease while paused, or choose a fresh directory.

`status` rejects stale files/exited PIDs and invalid headless telemetry. Headless
observations and actions perform the same check. Confirm `headless`, `batchMode`,
`graphicsDevice`, `cameraRenderCallbacks`, `mode`, `renderInterval`,
`gameLoaded` and `error`; a config write alone proves no effect. Camera callback
counters are diagnostics, not an FPS counter or an overall speedup. Preserve the
original mode for comparison. Removing the launch argument restores ordinary
game startup; uninstall only the companion entry/files, preserving other mods.

`python3 scripts/measure_render.py` compares normal/reduced/reduced/normal in
the same paused, loaded Linux process, with settling time and fixed sample
windows. It records process CPU time, actual camera callbacks and available DRM
render-engine time (deduplicated by client ID; unavailable is null). This tests
paused drawing overhead, not active-colony FPS or simulation throughput. It
rejects stale/unpaused state, retains partial failures, and restores the original
requested render policy. Use `--output` to select the active session directory.

## Measure the optimization loop

With a fresh paused, loaded disposable save, run:

```bash
python3 scripts/measure_steps.py --label baseline --samples 5 --warmup 1 --seconds 3
```

The script performs bounded native steps and records every sample, including
native-clock advancement, game-process CPU time, resident/swap memory, major
page faults, update frames, and the step's control-phase timings. It requires
positive native progress and a verified final pause; an error retains a failed
report. This measures the complete guarded step, including client startup,
control acknowledgment and pause verification. It is not a maximum-throughput
benchmark or an FPS measurement. A shorter acknowledgment is a harness latency
improvement, not evidence of faster simulation.

When testing an opt-in probe, add
`--require-log-marker '[CycleTrim][PerfProbe] resolved target:'` to refuse a run
where that marker is absent from the active session's log. Inspect the actual
enabled mod list as well: ONI can disable a changed local mod during its native
reinstall flow. Exit and deliberately re-enable the discovered entry; never
compare an unloaded candidate. A startup marker alone does not certify every
patch or mod feature.

Control-file notifications wake the companion on the game thread; background
watcher callbacks only set a flag. Pause/load transitions also publish status
promptly. The one-second reconciliation and every-update stop deadline remain
in place if notifications are unavailable or lost. Status reports
`controlNotifications`; compare the same save, enabled mods, speed and update cap.

Repeat baseline/candidate runs with warmup and inspect dispersion and paging.
Large fault/swap differences or concurrent host load invalidate a simple CPU
speedup conclusion. A small test colony does not establish late-game performance.
Managed heap size and RSS are not allocation rate, memory bandwidth, cache misses,
or memory saved by headless mode. Use the mod's opt-in probe to choose a hotspot,
then rerun functional tests and the same native workload after a change.

## Use a disposable copy of a save

Never overwrite a personal save for a test. Copy one into its own directory
under ONI's allowed local save root, then inspect and load that exact path:

```bash
python3 scripts/runtime.py call game_control \
  '{"domain":"launch","action":"start","path":"/allowed/save-root/Test/Test.sav","dryRun":true,"confirm":true,"resume":false}'
python3 scripts/runtime.py call game_control \
  '{"domain":"launch","action":"start","path":"/allowed/save-root/Test/Test.sav","confirm":true,"resume":false}'
```

Loading is asynchronous. A response accepting the request is not a loaded
colony. Verify status and pause after load before observations or commands.
No command in these scripts spawns resources or duplicants. Normal OniMcp
validation and confirmation rules apply to gameplay calls.

## Observe and watch from the AI's perspective

```bash
python3 scripts/runtime.py observe --view text --intent "Inspect the current colony"
python3 scripts/runtime.py serve
# Open http://127.0.0.1:8790/ only if you want to watch.
```

An observation first pauses and verifies pause, then reads `/active/index.md`
with state, a bounded virtual map, and `/active/dupes/index.md` through public
`world_editor`. By default it frames 32 by 24 cells around the current camera
position without moving the camera. `--bounds X1 Y1 X2 Y2` selects up to 1600
cells explicitly. Unknown terrain stays
unknown. The terminal, `latest.json`, append-only `observations.jsonl` and
browser share that returned map; the viewer does not reconstruct game state
from screenshots, interpolate movement or infer glyph meanings. Use OniMcp's
authoritative glyph lookup when interpreting a map for gameplay.

The optional spectator serves only `index.html` and `latest.json` on IPv4
loopback. It never connects to MCP, resumes the game or continuously polls
game state. It refreshes the last AI observation once per second only when
visible. It shows observation time, supplied intent, exact map/legend, object
records and the most recent explicit action result. There is no hidden
reasoning display. Without new AI observations the map intentionally stays
unchanged. Private saves, logs, paths and traces remain ignored local artifacts;
review and sanitize any evidence before publishing.

For a bounded smoke step after checking colony safety:

```bash
python3 scripts/runtime.py step --seconds 5 --speed 1
python3 scripts/runtime.py observe --view text --intent "Verify the short native game step"
```

`step` permits at most ten wall seconds, verifies an initial pause, requires a
fresh loaded companion and waits for its run-lease acknowledgment. The game
owns a deadline (requested duration plus four seconds for setup/communication)
and repeatedly pauses after expiry even if the client dies. The client also
attempts pause in `finally` and verifies it afterward. A frozen game main thread
cannot execute a watchdog; no hard real-time guarantee is made. An expired
lease keeps pausing until a new step lease is armed or the lease file is
deliberately removed while paused. This is a smoke-test primitive; use current
OniMcp condition-driven stepping for a supported autonomous controller.

Native pauses are counted separately from the MCP pause hold. A remaining native
pause is a failure, not a successful zero-progress step. After inspecting a
disposable save's initial pause, explicitly pass `--release-native-pause` if
needed; this uses ONI's existing launch/start resume route for the already
loaded exact save with `forceLoad=false`, and does not reload it. It can clear
native pause holds, so do not use it to bypass unexplained UI/modal pauses.
The companion releases only its own deadline pause when a new lease is armed.
Every successful step also requires a fresh paused status and actual positive
game-clock progress.

If authentication is enabled, put the token in `ONIHARNESS_MCP_TOKEN`; it is
sent only to the configured loopback HTTP endpoint, without proxies or redirects,
and is never written to receipts. Do not put secrets in explicit tool arguments.
Failed observations and child batches fail visibly rather than publishing a new
successful frame. `python3 scripts/verify.py` exercises the client without ONI;
real-game compatibility/performance evidence must be collected separately.
