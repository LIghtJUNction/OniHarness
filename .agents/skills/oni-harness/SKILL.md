---
name: oni-harness
description: Run and inspect Oxygen Not Included headless tests with OniHarness, including real native simulation fixtures, C# mod tests and recorded physics experiments. Use for game-free testing or requests to see headless simulation effects; full colony gameplay still requires a separately authorized game-control workflow.
---

# OniHarness

Use the actual harness and installed native library. Never launch ONI, Steam or
Unity Player as a fallback, and never substitute a made-up simulation result.
This skill runs controlled fixtures; it does not provide dupes, buildings, full
save loading or Harmony runtime support.

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
