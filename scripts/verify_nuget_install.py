"""Install the local NuGet artifacts into an isolated consumer and run tests."""

import argparse
import os
import subprocess
import tempfile
from pathlib import Path
from xml.sax.saxutils import escape


def verify(packages: Path, game_path: Path | None) -> None:
    packages = packages.resolve()
    expected = {"OniHarness.Core.0.1.0.nupkg", "OniHarness.Cli.0.1.0.nupkg"}
    if {p.name for p in packages.glob("*.nupkg")} != expected:
        raise ValueError("Provide exactly the Core and CLI 0.1.0 packages")
    with tempfile.TemporaryDirectory(prefix="oniharness-nuget-consumer-") as directory:
        root = Path(directory)
        environment = {**os.environ, "NUGET_PACKAGES": str(root / "package-cache")}

        def run(*arguments: str) -> None:
            print("+", " ".join(arguments), flush=True)
            subprocess.run(
                arguments, cwd=root, env=environment, check=True, timeout=120
            )

        (root / "NuGet.Config").write_text(
            '<configuration><packageSources><clear/><add key="verified-local" value="'
            + escape(str(packages), {'"': "&quot;"})
            + '"/><add key="nuget.org" value="https://api.nuget.org/v3/index.json"/>'
            "</packageSources><packageSourceMapping>"
            '<packageSource key="verified-local"><package pattern="OniHarness.*"/></packageSource>'
            '<packageSource key="nuget.org"><package pattern="Microsoft.*"/></packageSource>'
            "</packageSourceMapping></configuration>",
            encoding="utf-8",
        )
        (root / "Consumer.csproj").write_text(
            '<Project Sdk="Microsoft.NET.Sdk"><PropertyGroup>'
            "<TargetFramework>net10.0</TargetFramework><ImplicitUsings>enable</ImplicitUsings><DisableTransitiveFrameworkReferenceDownloads>true</DisableTransitiveFrameworkReferenceDownloads>"
            "</PropertyGroup><ItemGroup>"
            '<PackageReference Include="OniHarness.Core" Version="0.1.0"/>'
            "</ItemGroup></Project>",
            encoding="utf-8",
        )
        (root / "Tests.cs").write_text(
            """using OniHarness;
public static class PackageConsumerTests
{
    [HarnessTest]
    public static void PortablePolicy()
    {
        static int ClampRefresh(int requested) => Math.Clamp(requested, 1, 60);
        Check.That(ClampRefresh(0) == 1 && ClampRefresh(100) == 60, "Refresh limits changed.");
    }

    [HarnessTest]
    public static void NativeHeating(HarnessContext context)
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
""",
            encoding="utf-8",
        )
        (root / "global.json").write_text(
            '{"sdk":{"version":"10.0.100","rollForward":"latestFeature"}}',
            encoding="utf-8",
        )
        run(
            "dotnet",
            "tool",
            "install",
            "--tool-path",
            ".tools",
            "--configfile",
            "NuGet.Config",
            "OniHarness.Cli",
            "--version",
            "0.1.0",
        )
        run("dotnet", "build", "-c", "Release", "--configfile", "NuGet.Config")
        tool = str(
            root / ".tools" / ("oniharness.exe" if os.name == "nt" else "oniharness")
        )
        assembly = str(root / "bin/Release/net10.0/Consumer.dll")
        run(tool, "test", "--assembly", assembly, "--filter", "PortablePolicy")
        if game_path is not None:
            run(
                tool,
                "test",
                "--assembly",
                assembly,
                "--filter",
                "NativeHeating",
                "--game-path",
                str(game_path.resolve()),
            )
    print("PASS: isolated NuGet consumer compiled and the installed CLI ran its tests")
    if game_path is None:
        print("NOT RUN: native heating (provide --game-path)")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--packages", required=True, type=Path)
    parser.add_argument("--game-path", type=Path)
    options = parser.parse_args()
    verify(options.packages, options.game_path)
