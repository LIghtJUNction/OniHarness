# C# packages

OniHarness provides two .NET 10 packages:

| Package | Purpose |
| --- | --- |
| `OniHarness.Core` | Test attributes, assertions, isolated workers and the native fixture API |
| `OniHarness.Cli` | The `oniharness` .NET tool |

## Install from a local package directory

Until the NuGet upload is complete, use the verified local packages:

```sh
dotnet tool install --tool-path .tools --add-source ./packages OniHarness.Cli --version 0.1.0
dotnet add package OniHarness.Core --version 0.1.0 --source ./packages
```

After publication, omit `--add-source ./packages` and `--source ./packages` to
install from nuget.org. Publication status must be checked on the package page;
having an `.nupkg` file does not mean it is published.

## Write an attributed test

Create a class library targeting `net10.0` and reference `OniHarness.Core`:

```csharp
using OniHarness;

public static class MyModTests
{
    [HarnessTest]
    public static void RefreshIntervalStaysWithinBounds()
    {
        static int ClampRefresh(int requested) => Math.Clamp(requested, 1, 60);
        Check.That(ClampRefresh(0) == 1, "The minimum interval changed.");
        Check.That(ClampRefresh(100) == 60, "The maximum interval changed.");
    }

    [HarnessTest]
    public static void HeatingPreservesMass(HarnessContext context)
    {
        using var world = context.OpenWorld(new Scenario());
        world.Step(1);
        double initialMass = world.Snapshot().TotalMass;
        world.AddEnergy(8, 8, 1000);
        world.Step(5);
        var final = world.Snapshot();
        Check.That(final.Temperature.Max() > 300.1, "The gas did not heat.");
        Check.Near(initialMass, final.TotalMass, 0.01, "Heating changed mass");
    }
}
```

Build the test library, then run the policy test without starting ONI:

```sh
dotnet build -c Release
.tools/oniharness test --assembly bin/Release/net10.0/MyModTests.dll --filter RefreshInterval
```

The native heating test needs your installed, supported ONI files:

```sh
.tools/oniharness test --assembly bin/Release/net10.0/MyModTests.dll \
  --filter Heating --game-path /path/to/OxygenNotIncluded
```

Native fixtures do not run Unity, duplicant AI, buildings, or a colony save.
An unsupported installed binary fails explicitly. Game files are never included
in either package. See [capabilities](capabilities.md) for the tested boundary.

## Build and check packages

From a clean source checkout:

```sh
uv run --python 3.15 python scripts/verify.py
dotnet pack src/OniHarness.Core -c Release -o artifacts/nuget
dotnet pack src/OniHarness.Cli -c Release -o artifacts/nuget
uv run --python 3.15 python scripts/verify_distribution.py --packages artifacts/nuget
uv run --python 3.15 python scripts/verify_nuget_install.py --packages artifacts/nuget
```

Upload is a separate final step using a NuGet API key scoped to the two package
IDs. Never commit the key or include it in example notebooks or logs.

The install check uses a new package cache and maps OniHarness packages to the
local feed. Only Microsoft framework packages may come from nuget.org. It compiles a
separate consumer against Core and executes it with the packaged CLI. Add
`--game-path /path/to/OxygenNotIncluded` to also test native heating without
launching the game. It removes its temporary consumer after the check.
