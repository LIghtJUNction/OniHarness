<div align="center">
  <h1>OniHarness</h1>
  <p><strong>Test Oxygen Not Included mods without confusing fixtures, native simulation, and real gameplay.</strong></p>
  <p>Game-free tests · installed native solver · experimental headless ONI · repeatable evidence</p>

  [![Version](https://img.shields.io/badge/version-0.1.0-111111?style=flat-square)](CHANGELOG.md)
  [![.NET](https://img.shields.io/badge/.NET-10-111111?style=flat-square&logo=dotnet)](Directory.Build.props)
  [![Python](https://img.shields.io/badge/Python-3.15-111111?style=flat-square&logo=python)](pyproject.toml)
  [![License](https://img.shields.io/badge/license-AUL%201.0--draft.1-111111?style=flat-square)](LICENSE)

  [简体中文](README_ZH.md) · [Quick start](#quick-start) · [Evidence levels](#choose-the-right-evidence-level) · [Python](docs/python.md) · [Runtime](docs/runtime.md) · [Capabilities](docs/capabilities.md)
</div>

<p align="center">
  <img src="docs/images/physics-playground.gif" width="760" alt="Recorded native ONI physics fixture with water flow, partition removal and heating">
</p>

<p align="center"><em>A recorded fixture driven by the installed ONI native simulation library—not a game screenshot or colony save.</em></p>

## Choose the right evidence level

OniHarness exposes three deliberately different test paths:

| Level | What actually runs | Needs installed ONI | What it can prove |
| --- | --- | ---: | --- |
| **Game-free managed tests** | OniHarness workers and your portable .NET helpers | No | CLI, discovery, assertions, process isolation and logic that does not call Unity internals |
| **Native simulation fixtures** | The real installed ONI native simulation library | Yes | Selected gas, liquid, solid and heat behavior in small controlled worlds |
| **Experimental headless gameplay** | Steam starts the real ONI engine with `-batchmode -nographics` | Yes | Selected full-engine behavior, saves and OniMcp observations for a specific game/mod/save combination |

These levels are not interchangeable:

```text
metadata check < managed host < native simulation < real game runtime
```

A metadata match does not prove a method executes. A native fixture does not prove Unity lifecycle compatibility. A headless launch on one save does not prove every mod or graphics-dependent feature works.

> [!IMPORTANT]
> This is an early `0.1.0` preview, not a replacement for ONI. The verified native adapter currently supports **Linux x64, ONI build 744825**, with exact file fingerprints. Other builds fail explicitly instead of silently using a mismatched protocol.

## What you can test

- Gas mass conservation, liquid movement and solid heat transfer with the real native solver.
- Custom element parameters, cell replacement and energy injection in compact fixtures.
- Public static C# helpers marked with `[HarnessTest]`, isolated in worker processes.
- Selected installed game C# methods that execute in a .NET host without Unity internal calls.
- Method existence by type, name and parameter count using metadata only.
- Experimental real-engine headless sessions and pause-verified OniMcp observations.

Every scenario and repetition starts in a fresh process. Timeouts terminate the worker process tree. Crashes, unsupported adapters, empty selections and incomplete observations are failures. `--repeat` compares final native-state fingerprints between runs.

## Quick start

Requirements: .NET SDK 10. A local Linux ONI installation is required only for game-dependent paths.

```bash
git clone https://github.com/LIghtJUNction/OniHarness.git
cd OniHarness

dotnet build OniHarness.slnx -c Release
dotnet run --project tests/OniHarness.Tests -c Release --no-build
```

Point the harness at a local installation, then inspect it without loading game code:

```bash
export ONIHARNESS_GAME_PATH="$HOME/.local/share/Steam/steamapps/common/OxygenNotIncluded"

dotnet run --project src/OniHarness.Cli -c Release --no-build -- doctor
```

Run native scenarios three times and write an evidence report:

```bash
dotnet run --project src/OniHarness.Cli -c Release --no-build -- \
  test examples/scenarios \
  --repeat 3 \
  --report artifacts/native.json
```

Run a compiled mod-test assembly:

```bash
dotnet run --project src/OniHarness.Cli -c Release --no-build -- \
  test --assembly examples/ModTests/bin/Release/net10.0/ModTests.dll
```

Use `--game-path PATH` instead of the environment variable when preferred.

## Python API

Install the Python package with uv:

```bash
uv add oniharness
# or: uv pip install oniharness
```

The API can drive native JSON/C# tests and pause-verified OniMcp observations. Native tests still require the separate .NET backend and local game files; runtime observations connect to an already running OniMcp instance.

See the [Python guide and runnable examples](docs/python.md).

## Native physics playground

The source checkout includes a state-driven demonstration:

```bash
python3 scripts/physics_playground.py \
  --game-path /path/to/OxygenNotIncluded \
  --repeat 2 \
  --output artifacts/physics-playground
```

The controller waits for measured states before advancing:

```text
water falls
    ↓
three partition cells are removed
    ↓
water crosses into the second chamber
    ↓
energy is injected into an observed water cell
```

Each run writes a new trace directory. `trace.json` records every sampled cell, fixture input, action, check and file fingerprint. `summary.json` compares independent final states. A failed, unsupported or timed-out run remains a failure.

Open `index.html` for a dependency-free replay. Add `--gif` to render `replay.gif` and `overview.png` when Pillow is already installed.

To verify that the controller stops instead of issuing actions after insufficient observation:

```bash
python3 scripts/physics_playground.py \
  --game-path /path/to/OxygenNotIncluded \
  --phase-step-limit 20
```

Measured accepted fingerprints, phase checks and the intentionally rejected short-budget run are recorded in [playground verification](docs/verification/playground.json).

## Write a native scenario

```json
{
  "name": "My gas test",
  "width": 16,
  "height": 16,
  "steps": 50,
  "fill": {
    "element": "Oxygen",
    "mass": 1,
    "temperature": 300
  },
  "assertions": [
    {
      "kind": "massConserved",
      "absoluteTolerance": 0.01
    }
  ]
}
```

```bash
oniharness test my-test.json \
  --game-path /path/to/OxygenNotIncluded \
  --repeat 3
```

Units and geometry:

| Value | Contract |
| --- | --- |
| Temperature | Kelvin |
| Mass | kilograms |
| Energy | kilojoules |
| Origin | lower-left cell |
| Dimensions | 16–512 cells |
| Default step | 0.2 simulation seconds |
| Default process timeout | 20 wall-clock seconds |

Available assertions are `massConserved`, `totalMass`, `cellMass` and `cellTemperature`. Unknown JSON properties fail. CLI scenarios require at least one assertion and always reject non-finite or negative state.

Fixture elements are explicit test inputs. The small built-in table is not ONI's full material database. Phase transitions, diseases and radiation are disabled in this adapter. Supply your mod's element parameters when those values matter; see the [custom material example](examples/ModTests/ExampleTests.cs).

## Write C# tests

Create a .NET 10 class library referencing `OniHarness.Core`:

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

```bash
oniharness test \
  --assembly path/to/MyMod.Tests.dll \
  --game-path /path/to/ONI

oniharness test \
  --assembly path/to/MyMod.Tests.dll \
  --filter MyMaterial \
  --timeout 30
```

Tests may take zero parameters or one `HarnessContext`, and return `void` or `Task`. `async void` is rejected. Native worlds must remain on their creating thread, and each worker process may create only one native world.

`SetCell` and `AddEnergy` queue operations; call `Step` before observing effects. Heat can move to neighboring cells, so assertions must match the material and elapsed simulation time.

Existing net48 mod helpers can be linked into a net10.0 test project when portable. Passing there does not establish Mono or Unity compatibility. `InvokeGameStatic` executes an installed managed method in the .NET host without providing fake Unity internals.

## Metadata contracts

```bash
oniharness contract /path/to/Assembly-CSharp.dll \
  --type Sim \
  --method GetHandleIndex \
  --parameters 1
```

This checks metadata presence only. It does not validate overload parameter types, execute the method, inspect its body or prove Harmony patch compatibility.

## Experimental real-game runtime

The [runtime workflow](docs/runtime.md) launches the actual ONI engine through Steam with:

```text
-batchmode -nographics
```

It requires verified engine telemetry and never falls back to a graphical client. An optional text spectator displays the same observations returned to the AI.

Use it only after checking compatibility for the exact game build, save, DLC and mod set. Real headless execution can cover behavior the fixture adapter cannot, but it is slower, more stateful and still does not make every graphics-dependent feature valid.

## Reports and automation

`--report FILE` writes bounded JSON evidence with status, elapsed time, worker exit code and output. Native reports also include adapter identity, binary hash, completed steps, mass totals and final-state fingerprint.

Evidence labels are explicit:

| Label | Meaning |
| --- | --- |
| `metadata-only` | assembly metadata was inspected |
| `managed-host` | code executed in the .NET host |
| `native-sim` | installed ONI native simulation was called |
| `+game-code` | an installed managed game method was invoked |

None of those labels alone means “accepted in an actual colony.”

Exit codes:

| Code | Meaning |
| ---: | --- |
| `0` | every selected test passed |
| `1` | failure, timeout, crash or unsupported adapter |
| `2` | command, configuration or discovery error |

Game-free verification and package checks:

```bash
python3 scripts/verify.py
```

Add `--game-path PATH` to run real native scenarios three times and all C# examples. Public CI runs only the game-free portion and never downloads or uploads game files.

Test assemblies are trusted code, not a security sandbox. Process isolation contains crashes and timeouts; it does not remove file or network access.

## Install the CLI package

When a release package ZIP is available, extract it and install from its local package directory:

```bash
dotnet tool install \
  --tool-path .tools \
  --add-source ./packages \
  --ignore-failed-sources \
  OniHarness.Cli \
  --version 0.1.0

.tools/oniharness --help
```

`OniHarness.Cli` and `OniHarness.Core` packages may be attached to repository releases. That is separate from publication to nuget.org; inspect the specific release assets rather than assuming a public NuGet package exists.

## Repository map

```text
src/OniHarness.Core/      scenarios, workers, adapters and evidence
src/OniHarness.Cli/       command-line interface
src/OniHarness.Python/    Python-facing integration
examples/                 native fixtures and C# tests
scripts/                  verification, runtime and replay tools
docs/                     architecture, capabilities and evidence
.agents/skills/           agent workflow for choosing the right test level
```

OniHarness is independently usable and is also linked as an OniMods submodule.

## License and project status

OniHarness is an unofficial community project, not affiliated with Klei or Unity. No game DLLs, assets, decompiled implementation source or colony saves are included.

Original framework code uses [AUL 1.0-draft.1](LICENSE), a custom license that is not OSI-approved. Read [LICENSING.md](LICENSING.md) and [UPSTREAM.toml](UPSTREAM.toml). ONI, Unity, .NET and tested mods remain separate works under their own terms.
