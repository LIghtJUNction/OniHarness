# OniHarness

**ONI mod testing, with game-free fixtures and explicit real-game runs.**

[简体中文](README_ZH.md) · [Real-game / text view](docs/runtime.md) · [Architecture](docs/architecture.md) · [Supported capabilities](docs/capabilities.md)

For complete gameplay behavior, use the experimental [real-game workflow](docs/runtime.md):
start ONI through Steam, preserve native simulation and optionally reduce Unity
drawing. An optional text spectator displays the exact observations returned to
the AI. Compatibility and performance need testing per game/save/mod combination.

The original harness runs small, repeatable tests in isolated worker processes. It can drive
the **real installed ONI native simulation library** without launching ONI,
Steam, a Unity Player, a display server, or a colony save.

This is an early **0.1.0 preview**, not a complete replacement for ONI. The verified
native adapter currently supports **Linux x64, ONI build 744825**, with exact file
fingerprints. Other builds fail explicitly instead of silently using a mismatched
protocol. See [capabilities](docs/capabilities.md) before choosing a test.

## What you can test

- Gas mass conservation, liquid movement and solid heat transfer with the real native solver.
- Custom material parameters, cell replacement and energy injection in small fixture worlds.
- Your own C# mod helpers using `[HarnessTest]`, with process isolation and assertion failures.
- Selected installed game C# methods that can execute in a .NET host without Unity internal calls.
- The existence of a game method by type, name and parameter count, using metadata only.

Each scenario/repetition gets a fresh process. Timeouts terminate the worker
process tree; crashes, unsupported adapters and empty test runs are failures.
`--repeat` compares final native state fingerprints between runs.

## Run from source

Requirements: .NET SDK 10. A local Linux ONI installation is needed only for tests
that use game files. Public CI and the framework's own behavioral tests need no game.

```sh
git clone https://github.com/LIghtJUNction/OniHarness.git
cd OniHarness
dotnet build OniHarness.slnx -c Release
dotnet run --project tests/OniHarness.Tests -c Release --no-build

export ONIHARNESS_GAME_PATH="$HOME/.local/share/Steam/steamapps/common/OxygenNotIncluded"
dotnet run --project src/OniHarness.Cli -c Release --no-build -- doctor
dotnet run --project src/OniHarness.Cli -c Release --no-build -- \
  test examples/scenarios --repeat 3 --report artifacts/native.json
dotnet run --project src/OniHarness.Cli -c Release --no-build -- \
  test --assembly examples/ModTests/bin/Release/net10.0/ModTests.dll
```

Use `--game-path PATH` instead of the environment variable if preferred. `doctor`
hashes files and reports the adapter without loading game code.

## Install the CLI

Download the package ZIP from [Releases](https://github.com/LIghtJUNction/OniHarness/releases),
extract it, then install from that local package directory:

```sh
dotnet tool install --tool-path .tools --add-source ./packages \
  --ignore-failed-sources OniHarness.Cli --version 0.1.0
.tools/oniharness --help
```

The release provides `OniHarness.Cli` and `OniHarness.Core` packages. They are
downloadable release artifacts; publication to nuget.org is a separate step.

## Agent skill and physics replay (source checkout)

The repository includes an [oni-harness skill](.agents/skills/oni-harness/SKILL.md)
for agents working with headless tests. It distinguishes real native fixtures
from .NET-host tests and full colony gameplay. This skill and playground are
source-checkout additions after the v0.1.0 release; use the current repository.

```sh
python3 scripts/physics_playground.py --game-path /path/to/OxygenNotIncluded \
  --repeat 2 --output artifacts/physics-playground
```

Open the resulting `index.html` for a self-contained state replay: falling
water, removal of three partition cells, water crossing to another chamber,
then energy injection into an observed water cell. The controller waits for
measured target states and stops if a phase exceeds its step budget. Tests
check gravity, water mass, flow and heating. Intentional solid removal is
accounted for separately from unintended mass loss.

Each run gets a new trace directory. `trace.json` retains every sampled cell,
fixture inputs, actions, checks and file fingerprints; `summary.json` compares
independent final states. A failed/unsupported/timed-out run stays a failure.
Use `--phase-step-limit 20` for a deliberately short observation budget to
verify that the controller stops before issuing the next action.

Add `--gif` to also render `replay.gif` and `overview.png` when Pillow is already
installed. HTML needs no optional Python dependencies. The visualization is a
recorded physics fixture, not an ONI screenshot, save or duplicant playthrough.

![Recorded native physics fixture: water, partition removal and heating](docs/images/physics-playground.gif)

[Measured playground results](docs/verification/playground.json) include the
accepted binary hashes, phase checks and a deliberately rejected short-budget run.

## Write a scenario

```json
{
  "name": "My gas test",
  "width": 16,
  "height": 16,
  "steps": 50,
  "fill": { "element": "Oxygen", "mass": 1, "temperature": 300 },
  "assertions": [
    { "kind": "massConserved", "absoluteTolerance": 0.01 }
  ]
}
```

```sh
oniharness test my-test.json --game-path /path/to/OxygenNotIncluded --repeat 3
```

Temperatures are Kelvin, mass is kilograms, energy is kilojoules. Coordinates
start at the lower left; `x` increases right and `y` increases up. Dimensions are
16..512 cells. Regions apply after the default sealed boundary. The default step
is 0.2 simulation seconds; the process timeout is 20 wall-clock seconds.

Available assertions are `massConserved`, `totalMass`, `cellMass`, and
`cellTemperature`. Range assertions accept `min` and/or `max`; cell assertions
also require `x` and `y`. Invalid/unknown JSON properties fail. CLI scenarios
require at least one assertion and always check finite, non-negative state.

**Fixture elements are explicit test inputs.** The small built-in table is not
the game's complete material database. Phase transitions, diseases and radiation
are disabled in this adapter. To test your mod's parameters, provide your own
`elements` list in JSON or construct it in C#, keeping the required Vacuum, Void
and Unobtanium entries. See [the custom material example](examples/ModTests/ExampleTests.cs).

## Write C# tests

Create a .NET 10 class library referencing `OniHarness.Core` (a project reference
or the release package), then add public static methods:

```csharp
using OniHarness;

public static class MyModTests
{
    [HarnessTest]
    public static void MyMaterialDoesNotLoseMass(HarnessContext context)
    {
        using var world = context.OpenWorld(new Scenario());
        var before = world.Snapshot();
        world.Step(100);
        var after = world.Snapshot();
        Check.Near(before.TotalMass, after.TotalMass, 0.01);
    }
}
```

```sh
oniharness test --assembly path/to/MyMod.Tests.dll --game-path /path/to/ONI
oniharness test --assembly path/to/MyMod.Tests.dll --filter MyMaterial --timeout 30
```

Tests accept zero parameters or one `HarnessContext`, and return `void` or `Task`.
`async void` is rejected. Native worlds must be used synchronously on their
creating thread; only one native world may be created per worker process.
`SetCell` and `AddEnergy` queue operations; call `Step` before reading their effects.
Heat can move into neighboring cells, so choose assertions appropriate to the
material and elapsed simulation time.

For existing net48 mods, reference or link portable production helpers into a
net10.0 test project. This does not establish Mono or Unity runtime compatibility.
`InvokeGameStatic` executes the original installed managed method in the .NET
host; it supplies no fake Unity implementation. Methods requiring Unity internal
calls will fail. Harmony patch execution and full mod lifecycle are not supported
by this preview.

Static method checks are available separately:

```sh
oniharness contract /path/to/Assembly-CSharp.dll \
  --type Sim --method GetHandleIndex --parameters 1
```

This checks only metadata, not overload parameter types, method bodies or Harmony
compatibility.

## Reports and automation

`--report FILE` writes JSON with evidence, status, elapsed time, worker exit code,
bounded output and, for native scenarios, adapter, binary hash, completed steps,
mass totals and final state fingerprint. Evidence labels are `native-sim`,
`managed-host`, and `metadata-only`. C# tests using the context's native/game-code
entrypoints also report `+native-sim`/`+game-code` and the relevant fingerprints.
None is an in-game acceptance claim.

Exit codes: **0** all selected tests passed; **1** a test failed, timed out,
crashed or was unsupported; **2** command/configuration/discovery error. A report
is produced for completed test runs; setup errors are written to stderr.

Run `python3 scripts/verify.py` for game-free verification and package checks.
Pass `--game-path PATH` to additionally run real native scenarios three times
and all C# examples. CI runs only the game-free portion. Use your own runner
with your local installation for game-dependent tests; game files are never
downloaded or uploaded by the workflow.

Test assemblies are trusted code, not a security sandbox. Process isolation
contains crashes and timeouts; it does not restrict a test's file/network access.

## Project and license

OniHarness is independently usable and also linked as an OniMods Git submodule.
It is an unofficial community tool, not affiliated with Klei or Unity.

Original framework code is licensed under **AUL 1.0-draft.1**, as requested by the
maintainer. AUL is a custom license and is **not OSI-approved**. Read
[LICENSE](LICENSE), [LICENSING.md](LICENSING.md), and [UPSTREAM.toml](UPSTREAM.toml).
ONI, Unity, .NET and any mod you test remain separate works with their own terms.
No game DLLs, assets, decompiled implementation source or colony saves are included.
