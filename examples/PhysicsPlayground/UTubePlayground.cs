using System.Diagnostics;
using System.Text.Json;
using OniHarness;

public static class UTubePlayground
{
    /// <summary>
    /// Demonstrates communicating vessels level equalization: Two vessels initially separated by a partition.
    /// When the communication channel opens, asymmetric water levels equalize under gravity and lateral pressure
    /// until both vessels reach identical free surface liquid levels.
    /// </summary>
    [HarnessTest]
    public static void TestCommunicatingVesselsEquilibrium(HarnessContext context)
    {
        string outputDir = Environment.GetEnvironmentVariable("ONIHARNESS_UTUBE_OUTPUT")
            ?? Path.Combine(AppContext.BaseDirectory, "../../../../artifacts/u-tube-experiment");

        // Two connected chambers in a 24x24 world
        var scenario = new Scenario
        {
            Name = "Communicating Vessels Level Equalization",
            Width = 24,
            Height = 24,
            Fill = new() { Element = "Vacuum", Mass = 0, Temperature = 300 },
            Boundary = new() { Element = "Unobtanium", Mass = 20000 },
            Regions =
            [
                // Solid bottom bed (Y=2..3, X=4..19)
                new() { X = 4, Y = 2, Width = 16, Height = 2,
                    Cell = new() { Element = "Granite", Mass = 1000, Temperature = 300 } },
                // Left outer containment wall (X=6..7, Y=4..18)
                new() { X = 6, Y = 4, Width = 2, Height = 15,
                    Cell = new() { Element = "Granite", Mass = 1000, Temperature = 300 } },
                // Right outer containment wall (X=15..16, Y=4..18)
                new() { X = 15, Y = 4, Width = 2, Height = 15,
                    Cell = new() { Element = "Granite", Mass = 1000, Temperature = 300 } },

                // Central partition (1 cell thick, X=11, Y=4..12) separating the two vessels initially
                new() { X = 11, Y = 4, Width = 1, Height = 9,
                    Cell = new() { Element = "Granite", Mass = 1000, Temperature = 300 } },

                // Left Vessel (X=8..10, Y=4..11, 8 cells high) initially filled with water
                new() { X = 8, Y = 4, Width = 3, Height = 8,
                    Cell = new() { Element = "Water", Mass = 1000, Temperature = 300 } },

                // Right Vessel (X=12..14, Y=4..18) is initially empty (Vacuum)
            ],
            Assertions =
            [
                new() { Kind = "massConserved", AbsoluteTolerance = 0.5 }
            ]
        };

        using var world = context.OpenWorld(scenario);
        ushort waterIndex = scenario.ElementIndex("Water");

        var collector = new SimulationDataCollector(scenario);
        collector.AddProbe("LeftVesselCenter", 9, 4);
        collector.AddProbe("ConnectingGate", 11, 4);
        collector.AddProbe("RightVesselCenter", 13, 4);
        collector.MonitorColumn("LeftWaterLevel", 9);
        collector.MonitorColumn("RightWaterLevel", 13);
        collector.MonitorRegion("LeftVessel", 8, 4, 3, 12);
        collector.MonitorRegion("RightVessel", 12, 4, 3, 12);

        var initialSnapshot = world.Snapshot();
        collector.Record(0, initialSnapshot);
        double initialWaterMass = initialSnapshot.RegionMass(8, 4, 3, 12, waterIndex);

        // Open the communicating channel by removing the partition cells (X=11, Y=4..11)
        for (int py = 4; py <= 11; py++)
        {
            world.SetCell(11, py, new() { Element = "Vacuum", Mass = 0, Temperature = 300 });
        }

        // Step simulation until water equalizes across the communicating gate or reaches step limit
        const int maxSteps = 3000;
        const int batchSize = 25;
        while (world.StepsCompleted < maxSteps)
        {
            world.Step(batchSize);
            var snap = world.Snapshot();
            collector.Record(world.StepsCompleted, snap);
            double currentRight = snap.RegionMass(12, 4, 3, 12, waterIndex);
            int leftLevel = snap.ColumnLiquidLevel(9, waterIndex);
            int rightLevel = snap.ColumnLiquidLevel(13, waterIndex);
            if (currentRight > 8000 && Math.Abs(leftLevel - rightLevel) <= 1)
                break; // Reached dynamic communicating equilibrium!
        }

        var finalSnapshot = world.Snapshot();
        double finalWaterMass = finalSnapshot.RegionMass(0, 0, scenario.Width, scenario.Height, waterIndex);
        int finalLeftH = finalSnapshot.ColumnLiquidLevel(9, waterIndex);
        int finalRightH = finalSnapshot.ColumnLiquidLevel(13, waterIndex);
        double finalLeftMass = finalSnapshot.RegionMass(8, 4, 3, 12, waterIndex);
        double finalRightMass = finalSnapshot.RegionMass(12, 4, 3, 12, waterIndex);

        // Assertions:
        // 1. Total water mass conserved
        Check.Near(initialWaterMass, finalWaterMass, 0.5, "Mass conservation in communicating vessels");
        // 2. Substantial water flowed from left to right vessel
        Check.That(finalRightMass > 5000, $"Water flowed to right vessel: rightMass={finalRightMass:F1}kg");
        // 3. Free surface liquid levels reach equilibrium (LeftH == RightH within 1 cell)
        Check.That(Math.Abs(finalLeftH - finalRightH) <= 1,
            $"Communicating vessels equilibrium reached: LeftH={finalLeftH}, RightH={finalRightH}, LeftMass={finalLeftMass:F1}, RightMass={finalRightMass:F1}");

        // Save data
        Directory.CreateDirectory(outputDir);
        collector.SaveCsv(Path.Combine(outputDir, "communicating_vessels_data.csv"));
        collector.SaveJson(Path.Combine(outputDir, "communicating_vessels_data.json"));
    }

    /// <summary>
    /// Demonstrates arbitrary geometry U-tube fluid dynamics, ASCII map building and telemetry logging.
    /// </summary>
    [HarnessTest]
    public static void TestUTubeFluidDynamicsAndTelemetry(HarnessContext context)
    {
        string outputDir = Environment.GetEnvironmentVariable("ONIHARNESS_UTUBE_OUTPUT")
            ?? Path.Combine(AppContext.BaseDirectory, "../../../../artifacts/u-tube-experiment");

        // Construct U-Tube using arbitrary polygon / point regions and building placements
        var scenario = new Scenario
        {
            Name = "Arbitrary U-Tube Geometry and Fluid Telemetry",
            Width = 24,
            Height = 24,
            Fill = new() { Element = "Vacuum", Mass = 0, Temperature = 300 },
            Boundary = new() { Element = "Unobtanium", Mass = 20000 },
            // Building placement: Insulated Tile and Mesh Tile
            Buildings =
            [
                new() { BuildingDef = "InsulatedTile", X = 5, Y = 3, Element = "Granite", Mass = 400 },
                new() { BuildingDef = "InsulatedTile", X = 18, Y = 3, Element = "Granite", Mass = 400 },
            ],
            // Custom polygon region for curved/inclined basin base
            PolygonRegions =
            [
                new()
                {
                    Vertices =
                    [
                        new(4, 2), new(20, 2), new(19, 4), new(5, 4)
                    ],
                    Cell = new() { Element = "Granite", Mass = 1000, Temperature = 300 }
                }
            ],
            // Points region for discrete obstacle blocks in channel
            PointsRegions =
            [
                new()
                {
                    Points = [ new(11, 4), new(12, 4) ],
                    Cell = new() { Element = "Granite", Mass = 1000, Temperature = 300 }
                }
            ],
            Regions =
            [
                // Left vertical tube wall (X=4..5, Y=4..20)
                new() { X = 4, Y = 4, Width = 2, Height = 17,
                    Cell = new() { Element = "Granite", Mass = 1000, Temperature = 300 } },
                // Right vertical tube wall (X=18..19, Y=4..20)
                new() { X = 18, Y = 4, Width = 2, Height = 17,
                    Cell = new() { Element = "Granite", Mass = 1000, Temperature = 300 } },
                // Central barrier (X=11..12, Y=6..20)
                new() { X = 11, Y = 6, Width = 2, Height = 15,
                    Cell = new() { Element = "Granite", Mass = 1000, Temperature = 300 } },

                // Water injected in left vertical tube (X=6..10, Y=5..15)
                new() { X = 6, Y = 5, Width = 5, Height = 11,
                    Cell = new() { Element = "Water", Mass = 1000, Temperature = 300 } }
            ],
            Assertions =
            [
                new() { Kind = "massConserved", AbsoluteTolerance = 0.5 },
                new() { Kind = "totalMass", Min = 50000, Max = 1000000 }
            ]
        };

        using var world = context.OpenWorld(scenario);
        ushort waterIndex = scenario.ElementIndex("Water");

        var collector = new SimulationDataCollector(scenario);
        collector.AddProbe("LeftTubeEntrance", 8, 5);
        collector.AddProbe("BottomChannelThroat", 10, 5);
        collector.AddProbe("RightTubeBase", 15, 5);
        collector.MonitorColumn("LeftWaterColumn", 8);
        collector.MonitorColumn("RightWaterColumn", 15);
        collector.MonitorRegion("LeftTube", 6, 5, 5, 15);
        collector.MonitorRegion("RightTube", 13, 5, 5, 15);

        collector.Record(0, world.Snapshot());

        for (int i = 0; i < 20; i++)
        {
            world.Step(5);
            collector.Record(world.StepsCompleted, world.Snapshot());
        }

        var finalSnap = world.Snapshot();
        Check.That(finalSnap.TotalMass > 0, "Simulation produced valid world state.");

        Directory.CreateDirectory(outputDir);
        collector.SaveCsv(Path.Combine(outputDir, "u_tube_flow_dynamics.csv"));
        collector.SaveJson(Path.Combine(outputDir, "u_tube_flow_dynamics.json"));
    }
}
