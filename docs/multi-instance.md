# Resident complete-game instance controller

This experimental Linux controller is separate from `runtime.py`, which still
refuses a second game. It manages actual ONI/Unity processes and never substitutes
protocol clients for complete game clients. It does not implement OniOnline room
logic or modify CycleTrim.

## Current evidence and startup boundary

Game-free tests exercise the resident daemon, process/profile isolation checks,
DLL deployment decisions, native companion command handling through explicitly
labelled API doubles, real `/proc` measurements, and a real loopback TCP impairment
proxy. These are not four-player game acceptance.

The locally installed ONI uses Steam AppID **457140**. Local Steam history shows
an earlier second `-applaunch` request waiting for the first process and failing
with `AppError_16`. That historical receipt does not prove that the single-launch
wrapper route below cannot work. No direct-executable fallback is permitted.

Local UnityPlayer analysis found that `single-instance` is enabled by the presence
of its boot key, and that its lock is next to the real executable, resolved through
`/proc/self/exe`. An empty `single-instance=` value does not disable this mechanism.
Changing only the working directory or XDG root is therefore insufficient. The
controller preserves the original boot configuration, native singleton and Steam
authorization. Independent small installation views use one unchanged launcher
per instance and share the installed game data. Native concurrent acceptance is
still required; static analysis is not an executed multi-instance test.

Steam officially supports wrappers in a game's Launch Options, e.g.
`wrapper %command%` ([Valve support](https://help.steampowered.com/en/faqs/view/145A-FE54-F37B-278A)).
The controller requires this exact per-game option in ONI's Properties:

```text
python3 /absolute/OniHarness/scripts/steam_instance_entry.py %command%
```

Record the existing option and restore it after testing if desired. The wrapper
passes ordinary launches through unchanged. It enters its resident group mode only
with a matching private controller token. It verifies Steam ancestry; child games
remain in that official Steam launch chain and execute their normal Steam startup
and entitlement checks. It does not forge different Steam accounts. Same-account
processes are suitable for LAN roles only if the mod gives them separate connection
identities; they do not prove separate-account Steam P2P behavior.

The same exact Python entrypoint and final `%command%` may have `prime-run`,
`gamemoderun`, or both (in either order) in front. These known exec-style wrappers
remain in the Steam launch chain and are not removed or rewritten. Unknown
prefixes, wrapper arguments, extra trailing commands and malformed quoting are
rejected before launch; this is not a general shell-command parser.

There is no verified public shell API for changing this option in a running Steam
client. The controller reads only the active login's exact target app setting and
never modifies Steam's active `localconfig.vdf`, kills Steam, enables an internal
debug bridge, or guesses console commands. If the option is absent, startup fails
before launching anything and prints the exact setting required. A freshly
changed GUI option may need Steam to flush its normal saved configuration before
inspection sees it.

An authorized offline setup can use `scripts/steam_launch_options.py`. First
normally exit every game and request the existing client to exit with
`steam -shutdown`. A zero launcher exit code does not prove Steam is gone: wait
for the current user's Steam/client helpers to finish and the configuration file
to stop changing. The helper independently refuses mutations while any local
Steam or ONI process remains. It never terminates processes itself.

```sh
python3 scripts/steam_launch_options.py --config /steam/userdata/ACCOUNT/config/localconfig.vdf plan \
  --value 'python3 /absolute/OniHarness/scripts/steam_instance_entry.py %command%'
python3 scripts/steam_launch_options.py --config /steam/userdata/ACCOUNT/config/localconfig.vdf apply \
  --value 'python3 /absolute/OniHarness/scripts/steam_instance_entry.py %command%' \
  --expected-sha256 HASH_FROM_PLAN --journal /private/test-root/launch-option-restore.json
```

The structured parser selects only
`UserLocalConfigStore/Software/Valve/Steam/apps/457140/LaunchOptions`. A similarly
named `controller_config/457140` is unrelated. It replaces that value's token,
preserves all other bytes and the file mode, checks the expected SHA256, writes
atomically and reads back. Duplicate/malformed targets or concurrent edits fail.
The 0600 journal holds only the old option and edit metadata, not a full user
configuration or credentials. Restart the original Steam client normally and
verify the saved option again; authorization files are not altered by the helper.
If the launching shell lacks graphical environment variables, import only the
necessary display/DBus variables from the same user's existing authorized desktop
session, without printing the complete environment or inventing login data.

To remove the wrapper later, normally exit Steam again and use the helper's
`restore --journal PATH --expected-sha256 CURRENT_HASH`. Restore refuses to replace
a LaunchOptions value the user has since changed, and preserves unrelated Steam
updates instead of restoring a whole old config.

## Prepare and keep a group resident

Build the optional runtime companion against the user's installed game. Use a
built companion folder containing its DLL, `mod.yaml`, and `mod_info.yaml` as input;
do not run the single-profile installer against a live game. The controller
deploys independently into each test profile and does not edit the normal profile.
Each added mod source must likewise be an explicit built mod directory. Do not
point it at a repository root, a game directory or a Workshop download cache.

```sh
python3 scripts/instance_controller.py --root /tmp/oni-group init \
  --game-path /installed/OxygenNotIncluded \
  --companion /built/OniHarness.Runtime \
  --save /personal/save/Colony.sav \
  --mod '{"id":"MyMod","staticID":"My.Mod","source":"/built/MyMod"}'
python3 scripts/instance_controller.py --root /tmp/oni-group serve
```

`serve` stays in the foreground and takes a controller lock; use another terminal
for commands. Keep the root short because Unix socket paths have a native length
limit. It is created private to the current user. The default configuration has
one graphical host and three genuine headless guests, two CPUs per instance and
distinct declared connection ports/identities. `group.json` is the explicit plan;
the mod must consume the declared identities/ports if it needs network automation.
Harness itself uses isolated file controls and does not require OniMcp or open
another gameplay server.

```sh
python3 scripts/instance_controller.py --root /tmp/oni-group plan
python3 scripts/instance_controller.py --root /tmp/oni-group preflight --count 2
python3 scripts/instance_controller.py --root /tmp/oni-group start host
python3 scripts/instance_controller.py --root /tmp/oni-group start guest1
python3 scripts/instance_controller.py --root /tmp/oni-group reload host
python3 scripts/instance_controller.py --root /tmp/oni-group reload guest1
python3 scripts/instance_controller.py --root /tmp/oni-group verify host guest1 --seconds 1
```

First verify the graphical/headless pair. Only then start `guest2` and `guest3`,
load their independent copies, and verify all four together. `verify` requires
distinct live game PIDs, a graphical member, verified headless telemetry for each
headless member, exact paused test saves, positive native clock progress in every
original process, overlapping native step intervals, and final pause. A sequence
of independent successful runs never satisfies it. Receipts explicitly say
`completeGameCount`, `protocolClients: 0`, and whether game reconnection was tested.

Each profile contains separate XDG configuration, native Unity preferences, mod
enablement manifest, copied mod payloads, disposable save, controls, working
directory and logs. The source save is read only. A profile lists allowed save
paths and SHA256 digests; the in-game companion refuses unapproved paths, path
links, mismatched bytes, crossed identities or stale run nonces. `load` acceptance
is followed by exact save-path/loaded/pause verification. No cheats or resources
are generated. Fresh-colony creation remains an ordinary native game/menu flow.
Repeated preparation preserves that private profile's native discovery records,
labels, versions and crash metadata. Only the explicitly configured
local mods are enabled for the selected DLC; all other discovered entries remain
disabled. Deleting disabled Workshop records would make the game rediscover them
and display repeated native Mod Events notifications. The controller does not
erase Workshop directories, dismiss those notifications or alter the normal
profile's manifest.
Native `ReinstallPending` is an operation, not passive registration metadata. An
explicit local payload installed offline by the controller is marked installed
only after the complete target file inventory and hashes match the plan; a pending directory
reinstall may be resolved only when it points to that exact owned payload.
External reinstall directives, unplanned pending operations and native
uninstall/failed-install states are not silently erased. They block deployment
for review. Native crash counters and load-in-progress flags remain preserved.
Before preparation changes any payload, the writable instance, profile, control,
manifest, local-mod and disposable-save paths must remain under that instance's
owned directory without symlink ancestors. A redirected `Local` directory or
cross-instance profile link is rejected before deletion or copying.

## Reuse, update and recover

```sh
python3 scripts/instance_controller.py --root /tmp/oni-group start guest1
python3 scripts/instance_controller.py --root /tmp/oni-group reload guest1
python3 scripts/instance_controller.py --root /tmp/oni-group restart guest1
python3 scripts/instance_controller.py --root /tmp/oni-group status
python3 scripts/instance_controller.py --root /tmp/oni-group stop-all
python3 scripts/instance_controller.py --root /tmp/oni-group daemon-stop
```

A native-generated private checkpoint can be reloaded at its original exact path
when a probe's read-only contract requires it. First stop that instance normally,
then register the explicit file and SHA256 from its most recent verified native
run:

```sh
python3 scripts/instance_controller.py --root /tmp/oni-group approve-save host \
  '/tmp/oni-group/instances/host/xdg/unity3d/Klei/Oxygen Not Included/save_files/OniOnlineHarness-PID/archive-source-N.sav' SHA256
python3 scripts/instance_controller.py --root /tmp/oni-group start host
python3 scripts/instance_controller.py --root /tmp/oni-group reload host --path \
  '/tmp/oni-group/instances/host/xdg/unity3d/Klei/Oxygen Not Included/save_files/OniOnlineHarness-PID/archive-source-N.sav'
```

The `ownedSaveApprovals` instance configuration records the exact canonical path,
hash, originating owned PID/start ticks and run nonce. A separate controller state
ledger records strictly adopted Steam-child origins and approvals issued through
the CLI; group configuration alone cannot invent either proof. Checkpoint origins
also require normal fresh private-root/graphics checks and actual loaded DLL
hashes; cleanup-only PID adoption cannot issue approvals. Registration requires the instance and
its Steam chain to be stopped, an existing `.sav` in that private
`save_files/OniOnlineHarness-PID` directory, and the most recently recorded owned
native origin. Symlinks, external/cross-instance paths, traversal, conflicting
approvals and wrong hashes are rejected. Preparation verifies those files before
deployment and again before publishing the native profile. It retains the exact
files without copying, renaming or overwriting them. Combined with the ordinary
disposable copy, the explicit native allowlist cannot exceed 64 entries. Multiple
approvals require an explicit `reload --path`; an unapproved path cannot start a
new game. Already-started profiles cannot acquire new approvals. Ambiguous
selection is rejected before a new game starts. This does not
authorize loading an arbitrary user save or weaken probe checkpoint receipts.
The native companion rechecks its cached private-path approval and SHA256 inside
the queued loading-overlay callback immediately before the native load. Changing
the file, redirecting it through a link, or expanding the disk profile while it is
queued cannot authorize different bytes.

`start` reuses a live process when its deployed mod bytes are unchanged and its
actually loaded DLL hashes match. A changed mod requires orderly native pause and
quit, verified process exit, deployment into only that profile, and relaunch from
the resident Steam wrapper. Failed exit prevents overwrite. Hash receipts come
from the loaded assemblies' actual file locations, not only desired build files.
An unknown ONI process owned by another task blocks new launches/deployment.
No automatic force kill exists.

The daemon can be stopped and restarted without automatically killing games.
Its private state retains PID plus Linux process start ticks to reject PID reuse.
Native Unity telemetry must also match the currently owned Steam chain and its
actual descendants before the controller adopts a game. A pending chain blocks
redeployment, including when readiness fails before native telemetry appears.
Gameplay commands require fresh telemetry. Only pause/quit may use stale telemetry,
and only for a previously confirmed native PID, process start ticks and run nonce
that still belong to the currently live owned Steam chain. Stale data cannot
authorize initial adoption, a load or a simulation step; a missing native response
still fails explicitly instead of forcing an exit.
The Steam wrapper stays resident until its children exit. `stop-all` requests
native exits before stopping that wrapper. If a confirmed quit exceeds the exit
deadline, the command still fails and writes an immutable failure receipt. For
up to five minutes the resident daemon can observe that same failed attempt:
only after its exact native/runtime incarnations, observed descendants, other
roles and pending launch requests are gone may it request one normal shutdown
of the original supervisor. Its PID, start ticks and nonce must still match.
The daemon records `eventual_stopped` only after the supervisor also disappears;
an acknowledgement alone is insufficient. It never retries a native pause/quit,
sends a process signal or changes the original failed result. Unreadable process
identity defers cleanup; a new generation supersedes the attempt, and deadline
expiry requires manual review. This tail recovery has game-free regression
coverage; the earlier real late quit was observed and cleaned up manually.
A game-side bounded step owns its
deadline; losing the controller cannot leave that step intentionally unbounded.

Optional external mod probes should use a fresh directory each launch. In the
instance's `gameArgs`, use a directory template such as:

```json
["--onionline-probe={controlDir}/runs/{runNonce}/online-probe"]
```

The Steam wrapper resolves only these two placeholders under that instance's
control directory and records the result in `wrapper-entry.json` as
`resolvedGameArgs` and `externalControlRunNonce`. This namespace nonce belongs to
the external path; the companion independently generates its native command
nonce. Traversal, unknown templates and an already existing target fail. Old
directories and their commands remain retained, and are never copied into the
new directory. Copy only the explicit read-only fixture into the resolved path
after startup, then issue fresh commands after the expected native save is loaded.
An unchanged-DLL `start` reuses its existing directory and native process.
Absolute legacy flags remain accepted but do not provide this replay isolation.

## Memory and network conditions

The controller samples the complete owned process group every two seconds:
RSS, proportional PSS, swap, minor/major page faults and CPU; system available RAM,
swap and swap-in/out counters are recorded alongside rates. `resources.jsonl`
retains samples. PSS avoids counting shared installation pages as wholly private.
Default capacity planning reserves 3 GiB per game plus 1 GiB for the host. If
measured available RAM plus owned PSS cannot support the requested concurrent
count, startup fails with the actual measurement. `resourceLimits` in `group.json`
can additionally bound RSS/PSS/swap, reserve memory and major-fault rate. The
controller reports violations and refuses expansion; it never kills unknown
processes or disguises serial tests as a successful concurrent group.

No screenshots, texture probes or world-sized dumps are enabled. Headless uses
batch mode, Null graphics and verified zero camera render callbacks. A graphical
fallback is never attempted. The Steam wrapper limits CPU affinity, Unity job
workers and common native thread-pool variables for each instance; native subsystems
can still create bookkeeping threads, so this is not a universal thread-count cap.

For a LAN TCP test, direct the mod's clients through an explicit loopback proxy:

```sh
python3 scripts/instance_controller.py --root /tmp/oni-group proxy-start guest1 28000 --listen-port 28101
python3 scripts/instance_controller.py --root /tmp/oni-group proxy-profile guest1 \
  '{"enabled":true,"latency_ms":1500,"jitter_ms":250,"bytes_per_second":2048,"seed":7}'
python3 scripts/instance_controller.py --root /tmp/oni-group proxy-profile guest1 '{"enabled":true,"disconnect":true}'
python3 scripts/instance_controller.py --root /tmp/oni-group proxy-profile guest1 '{}'
python3 scripts/instance_controller.py --root /tmp/oni-group proxy-stop guest1
```

The proxy is bounded and persistent, with actual traffic counters, latency,
jitter, bandwidth limits, blackhole/backpressure, disconnect and recovery. It
impairs each direction per TCP read chunk. TCP preserves byte order; this is not
IP packet loss, UDP or Steam P2P emulation. Disconnect recovery requires the
actual client to reconnect. Proxy socket tests prove transport behavior only;
native room membership, state recovery and duplicate-command handling need the
mod's own observable assertions. They are never automatically marked passed.

Run the regression suite with `python3 scripts/verify.py`.

On 2026-10-21, one genuine headless instance passed the configured Steam wrapper
and full steam-launch-wrapper/reaper/sniper chain, isolated installation and
profile checks, exact loaded DLL hashes, paused disposable-save loading,
unchanged-DLL process reuse, same-process reload, a new-process restart and
normal exit of the native game plus its owned runtime processes and group
supervisor. The controller remained resident. The original saves retained their
hashes. A read-only OniOnline test fixture also retained its cancelled state
across reload/restart; this is not multiplayer acceptance. The optional external
directory templates were added afterward and initially had game-free regression
evidence only.
A subsequent real DLL update deployed the normal OniOnline build into only the
stopped guest profile, started a new native process through the full Steam chain,
verified its changed loaded hash against the normal package, loaded the same
owned save paused without advancing its clock, and normally reclaimed its chain.
The other three prepared profiles retained their earlier payloads.

A subsequent native probe run also exercised the external directory template:
one genuine Steam-launched headless game received a fresh empty probe directory,
with its external directory nonce distinct from the companion's native nonce.
The 33-check smoke test used the normal new-colony screens and three protocol
clients. It verified paused native work revocation, rejection of an old
confirmation after regrant, restoration through a fresh two-phase request, and
a newly written disposable checkpoint. The native clock did not advance during
the revocation checks. After normal exit all five owned game/runtime/supervisor
processes were reclaimed; global Unity and Klei preference hashes were unchanged.
The private guest profile was restored to the normal DLL and no probe arguments,
without starting another game. Detailed receipts are retained separately under
`mods/OniOnline/.local-tests/native-work-revocation-20261021/` in the OniMods
workspace. This one-game/three-protocol-client test is not multi-native acceptance.

A later graphical run exercised one native host with up to four protocol clients.
Its corrected probe completed 112 driver checks covering network task creation,
withheld acknowledgement, reconnect/replay idempotency, revocation/regrant,
natural native digging completion, fresh checkpoints and a read-only starter
archive with `ImportReady=false`. Its task-input handler checks passed, but an
original Mod Events notification obscured the screenshot. That run does **not**
pass visible/actionable task-UI acceptance. Native event-cache preservation and
stale cleanup have subsequent game-free regressions. At that point native UI and
completed-checkpoint reload were separate pending gates. The earlier
probe null-reference failure and its original receipts remain retained.

The completed checkpoint subsequently passed a fresh Steam-launched headless
process reload. The actual private `Assembly.Location` matched the recorded
`f2c2d98...` DLL hash. A single read-only completion inspector found the dug cell
still excavated and no resurrected native order, marker, candidate or saved task
journal entry. The game stayed paused at 59.3831901550293 seconds; no step or new
save was requested. Null graphics and zero camera callbacks were confirmed.
After normal quit, the native process, Steam chain, supervisor and their observed
descendants were gone. Both source checkpoints, the disposable copy and global
Unity/Klei preference hashes remained unchanged. The old probe does not expose
an independent duplicant-count sample; it loaded the unchanged three-starter
checkpoint and received no spawning or printing command. Its complete owned tree
sample was 3488902 KiB PSS, falling to 7217 KiB for the resident controller after
exit. This closes the completed-task reload gate, while visible UI and concurrent
complete-game acceptance remain open. Evidence is retained under
`native-remote-work-20261021/completion-headless-reload/success-native-2755496/`
in the same ignored local test directory.

A later graphical run loaded the new exact-checkpoint companion and a frozen
OniOnline probe through Steam. Its six inspected 1600x900 images had no native
Mod Events obstruction. The original smoke still failed on an outdated
`engineStory` text predicate; independent image review confirmed that the real
engine manual was visible. The failed original receipt remains unchanged.
Long-chat anchor/latest controls, Archive v2, private checkpoints A/B, empty-object
Lifecycle and Steam public-room self-discovery have separate successful receipts.
Source inspection preserved the native fingerprint but reported the starter
ineligible while its ordinary welcome emote/navigation initialization continued.
The capacity inspection also preserved its fingerprint but correctly reported the
fixed 256x384 request did not fit that run's 736x304 native grid. These are not
source-transfer or world-capacity acceptance. No chore was cancelled and no
additional duplicant was spawned or individually loaded by a transfer test.

That graphical quit was acknowledged, then exceeded the controller's 30-second
exit deadline before naturally completing the same quit. The timeout failure is
retained. Native `Game.OnApplicationQuit`, normal runtime-chain exit and all
observed PID/start-tick incarnations disappearing were subsequently verified;
no second quit or OS signal was sent. The empty supervisor was then shut down
normally. The observed peak owned-tree PSS plus SwapPSS was 4109816 KiB, so the
private group's forecast was raised to 4 GiB per game plus 1 GiB reserve.
One game now requires 5 GiB and four require 17 GiB, exceeding this host's
15.346 GiB physical RAM. This is a limit for the measured current colonies, not
proof about every possible smaller native world. The exact B approval was issued
and preserved, but the second process's sole admission check found only about
4.45 GiB and refused startup. Exact B reload, new-Game Lifecycle and strict
preparation remain pending. Detailed evidence is retained under
`native-origin-load-20261021/{first-process,second-process}/`.

The following private candidate was prepared offline with exactly 20 planned
files, OniOnline hash `08d86120e1d4...` and the same `18f34d233cbb...` companion.
It retains the exact B approval and 4 GiB per-game forecast. At preparation it had
not been loaded. Its documentation is the preparation-time snapshot, which differs from
the subsequently refreshed ordinary package validation document. Future exact-B
testing inserts the read-only navigation-quiescence gate after the new Game's
empty Lifecycle and before strict preparation; no duplicant import is authorized
by preparing this profile.

After a real capacity improvement, one fresh admission and the at-start check
passed the unchanged 5 GiB gate. A new Steam-launched headless process loaded
that candidate and the exact approved B at its original path. Actual assembly
locations/hashes, Null graphics, zero camera callbacks and the private profile
were verified. The original three native IDs remained 395/397/399 and time stayed
51.616641998291016 seconds. Source inspection and same-Game B-bound empty Lifecycle
passed without changing their fingerprints. Source eligibility still refused
because native navigation was not stopped/initialized on the floor, despite the
selected starter now having its native idle chore. Navigation-quiescence and
strict preparation were therefore not attempted; no simulation step or new save
was requested. This probe exposes the colony owner and selected native ID but
not all three live stable-origin bindings, so those bindings remain unverified.
The single normal stop completed inside its deadline and all five observed
native/runtime/supervisor incarnations were gone. Automatic late-timeout recovery
was not triggered by this run. B, the earlier checkpoint, copied archive/source
and global preferences retained their hashes. The sampled owned-tree peak PSS
plus SwapPSS was 3551242 KiB across 82 valid samples; the 4 GiB forecast remains.
Evidence is retained under `native-origin-load-20261021/navigation-process/`.

A subsequent, separately frozen diagnostic probe (`33a27ff9f798...`) passed one
Steam headless exact-B reload with the same companion and unchanged paused time.
Its bounded reads verified all three existing live stable-origin bindings with
exact native references. The selected original matched the old real Archive v2
manifest's five origin fields, native ID, world and active save. This comparison
does not certify the entire archive or the other two originals' historical
manifest fields. Diagnostic references, bounded snapshots and the full colony
fingerprint stayed unchanged, with no read errors. The cached Navigator matched
its controller entry but remained initialized without a running state; empty
async queues did not prove quiescence. Source eligibility remained rejected.
The independent capacity inspection used the actual source world's 160x274
dimensions and still found no existing grid rectangle, despite an available
native hex. No navigation fence, strict preparation, step or save was requested.
One normal quit reclaimed every observed owned native/runtime/supervisor
incarnation; B/A, the configured original, archive/source and global preference
hashes remained unchanged. Whole-tree peak PSS plus SwapPSS was 3542162 KiB across
85 valid samples. Evidence is retained under
`native-readonly-diagnostics-20261021/native-process/`.

The loaded owned tree was observed at about 3199 MiB PSS in one sample, including
the runtime chain, supervisor and controller; this is not a native-only RSS
measurement or a hard memory bound. The earlier lifecycle peak sampled tree PSS
with all processes valid was about 3317 MiB; the later completed-save reload
sample above was about 3407 MiB. The default 3 GiB forecast is an estimate.
Later samples had lower resident PSS and substantial swapped memory. Fresh
two/four-game admission remained rejected: available RAM plus owned PSS was
about 5.2 GiB, below the 7/13 GiB budgets including reserve. No unknown applications
were closed and no threshold was reduced to force a pass.

Unexecuted native gates remain: 1 graphical + 1 headless simultaneously, then
4 simultaneously loaded games, single-game restart with peers continuing, and
native room disconnect/reconnect. No protocol clients substitute for those
checks. Local detailed receipts are under
`artifacts/multi-instance-20261021/`; they include startup failures later fixed
in the controller, as well as the successful single-instance lifecycle evidence.
