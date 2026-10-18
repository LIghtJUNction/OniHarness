using OniHarness;

public static class ExampleTests
{
    [HarnessTest]
    public static void PolicyCanRunWithoutGameFiles()
    {
        // Replace this policy with your mod's production helper or project reference.
        static int ClampRefresh(int requested) => Math.Clamp(requested, 1, 60);
        Check.That(ClampRefresh(0) == 1 && ClampRefresh(100) == 60, "Refresh limits changed.");
    }

    [HarnessTest]
    public static void InstalledGameMethodRunsWithoutUnity(HarnessContext context)
    {
        object? actual = context.InvokeGameStatic("Sim", "GetHandleIndex", 0x12345678);
        Check.That(actual is int index && index == 0x345678, "Installed Sim.GetHandleIndex returned an unexpected value.");
    }

    [HarnessTest]
    public static void NativeEnergyRaisesGasTemperature(HarnessContext context)
    {
        var scenario = new Scenario();
        using var world = context.OpenWorld(scenario);
        world.Step(1);
        var initial = world.Snapshot();
        world.AddEnergy(8, 8, 1000);
        world.Step(5);
        var final = world.Snapshot();
        Check.That(final.Temperature.Max() > 300.1, "Adding energy did not heat the native gas.");
        Check.Near(initial.TotalMass, final.TotalMass, 0.01, "Heating unexpectedly changed mass");
    }

    [HarnessTest]
    public static void NativeCellReplacementChangesTemperature(HarnessContext context)
    {
        using var world = context.OpenWorld(new Scenario());
        world.Step(1);
        world.SetCell(5, 5, new() { Element = "Oxygen", Mass = 1, Temperature = 600 });
        world.Step(10);
        Check.That(world.Snapshot().Temperature.Max() > 300.1, "Cell replacement did not reach the native simulation.");
    }

    [HarnessTest]
    public static void CustomModMaterialConductsHeat(HarnessContext context)
    {
        var custom = new ElementDefinition { Name = "MyModMetal", Id = 123456789,
            State = ElementState.Solid, SpecificHeatCapacity = 1, ThermalConductivity = 100 };
        var scenario = new Scenario
        {
            Elements = [.. ElementDefinition.Fixtures(), custom],
            Fill = new() { Element = custom.Name, Mass = 1000, Temperature = 300 },
            Regions = [new() { X = 1, Y = 1, Width = 7, Height = 14,
                Cell = new() { Element = custom.Name, Mass = 1000, Temperature = 400 } }]
        };
        using var world = context.OpenWorld(scenario);
        world.Step(50);
        var final = world.Snapshot();
        Check.That(final.Temperature[final.Index(7, 8)] < 400 && final.Temperature[final.Index(8, 8)] > 300,
            "Custom material did not transfer heat through the native solver.");
    }
}
