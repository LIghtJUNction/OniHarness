---
name: oni-harness
description: Test ONI mods with OniHarness using game-free native fixtures or explicit Steam-launched real-game tests with reduced rendering and an optional AI text-map spectator. Use for headless testing, rendering-cost checks, recorded physics experiments and shared AI observations.
---

# OniHarness

Choose the runtime explicitly. Native fixtures never launch ONI, Steam or Unity
as a fallback. When the user authorizes running the game, use the real-game route
below. Never substitute a made-up simulation result or equate fixture behavior
with full-game compatibility.

## Real-game route

For gameplay, reduced drawing or watching AI text maps, read `docs/runtime.md`.
It describes `scripts/install_runtime.py` and explicit `scripts/runtime.py`
commands. Start the actual game through Steam only. Verify a fresh companion
status, exact save/mod combination and paused loaded state. Use a disposable
save copy and short guarded steps. Do not spawn resources or duplicants.

Use `observe --view text` to show the exact paused map returned to the AI, or
`--view none` to record without displaying a terminal map. `serve` is an optional
read-only spectator of those same files. It does not poll or control the game.
The game-control skill's pause/observe/act/short-step/pause/verify rules and
authoritative glyph lookup still apply when interpreting glyphs or playing.

Measure normal versus reduced mode in the same process/workload; a changed
config or launch flag is not proof of savings. Keep normal/reduced/no-graphics
results separate. Do not call untested features fully compatible. Logs, save
paths and full observations remain private local artifacts unless sanitized.

## Locate and inspect

Find the checkout in the current OniHarness repository, `tools/OniHarness` in
OniMods, or a caller-provided location. An installed skill alone does not contain
the framework. Read that checkout's `docs/capabilities.md` and `AGENTS.md`.

Use `ONIHARNESS_GAME_PATH` or an explicitly provided local game path. When missing,
check the usual local Steam ONI installation directories before asking. Do not
download game files or scan the whole home directory. Run `doctor` to verify the
installed fingerprints; unsupported versions are a reported blocker, not a reason
to relax the adapter check.

All commands below run from the OniHarness checkout. Prefix commands with a
workspace-required wrapper such as RTK when applicable.

```sh
dotnet run --project src/OniHarness.Cli -c Release -- doctor --game-path /path/to/ONI
```

## Choose the smallest useful run

- Framework regression: `python3 scripts/verify.py` (no game needed).
- Real-library regression: `python3 scripts/verify.py --game-path /path/to/ONI`.
- One scenario: CLI `test examples/scenarios/sealed-gas.json --game-path /path/to/ONI --repeat 3 --report artifacts/results.json`.
- C# mod logic: CLI `test --assembly /path/to/MyMod.Tests.dll --game-path /path/to/ONI --filter TEXT`.
- Observe/action/step experiment with recorded states:

```sh
python3 scripts/physics_playground.py --game-path /path/to/ONI \
  --output artifacts/physics-playground --repeat 2
```

The playground uses the normal isolated test worker. It records falling water,
removes three partition cells, observes water crossing into another chamber,
then adds energy to a measured water cell. Its HTML viewer is a local data
replay, not a game screenshot or live colony UI. `--gif` additionally produces a
GIF when Pillow is already available; do not install optional dependencies
without a concrete need.

For a new experiment, start with a bounded fixture and explicit checks. Read the
core API or example only as needed. `SetCell` and `AddEnergy` queue changes; call
`Step` before observing effects. Keep one world per worker and use it on its
creating thread. Geometry edits can intentionally change total mass: distinguish
that from unintended water loss. Inspect selected cells before changing them.

## Judge the observed result

Check exit status, test status, adapter/hash, completed steps and actual measured
state. A clean process exit, a generated picture or a metadata contract is not a
physics success. Empty selections, crashes, timeouts, unsupported profiles and
failed assertions remain failures. Preserve failed traces for diagnosis.

For repeat runs compare state fingerprints, not image timestamps or wall time.
Report native-solver evidence separately from .NET-host method execution and
static metadata. Qualify timings by fixture and steps; do not turn them into an
ONI FPS claim. Link the result JSON and replay, summarize measured changes, and
state that full colony/dupe gameplay was not exercised. Keep game files and
private saves out of commits and public artifacts.
