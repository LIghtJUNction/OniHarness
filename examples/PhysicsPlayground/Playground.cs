using System.Diagnostics;
using System.Text.Json;
using OniHarness;

public static class Playground
{
    [HarnessTest]
    public static void FallingWaterGateAndHeat(HarnessContext context)
    {
        string output = Environment.GetEnvironmentVariable("ONIHARNESS_PLAYGROUND_OUTPUT")
            ?? throw new InvalidOperationException("Run scripts/physics_playground.py to choose an output directory.");
        Check.That(int.TryParse(Environment.GetEnvironmentVariable("ONIHARNESS_PLAYGROUND_PHASE_LIMIT") ?? "5000", out int phaseLimit)
            && phaseLimit is >= 20 and <= 5000 && phaseLimit % 20 == 0, "Phase limit must be a multiple of 20, between 20 and 5000.");
        var scenario = new Scenario
        {
            Name = "Falling water, a removable partition, and heat",
            Width = 24, Height = 24,
            Fill = new() { Element = "Vacuum", Mass = 0, Temperature = 300 },
            Regions =
            [
                new() { X = 12, Y = 1, Width = 1, Height = 22,
                    Cell = new() { Element = "Granite", Mass = 1000, Temperature = 300 } },
                new() { X = 4, Y = 16, Width = 6, Height = 4,
                    Cell = new() { Element = "Water", Mass = 500, Temperature = 300 } },
                new() { X = 17, Y = 4, Width = 2, Height = 4,
                    Cell = new() { Element = "Granite", Mass = 1000, Temperature = 450 } },
                new() { X = 19, Y = 4, Width = 2, Height = 4,
                    Cell = new() { Element = "Granite", Mass = 1000, Temperature = 300 } }
            ]
        };
        var watch = Stopwatch.StartNew();
        var frames = new List<RecordedFrame>();
        var events = new List<RecordedEvent>();
        var checks = new List<RecordedCheck>();
        using var world = context.OpenWorld(scenario);
        ushort water = scenario.ElementIndex("Water");
        Capture("initial");
        AdvanceUntil("falling", frame => frame.WaterCentreY < 3, phaseLimit);
        var initial = frames[0];
        var settled = frames[^1];
        checks.Add(new("Gravity lowers the water's centre of mass", settled.WaterCentreY < initial.WaterCentreY - 5,
            settled.WaterCentreY, $"< {initial.WaterCentreY - 5:F3} cells"));
        checks.Add(new("Water mass is conserved before edits", Math.Abs(settled.WaterMass - initial.WaterMass) <= 0.5,
            settled.WaterMass, $"{initial.WaterMass:F3} +/- 0.5 kg"));
        events.Add(new(world.StepsCompleted, "Remove partition cells (12,1), (12,2), (12,3)", null));
        for (int y = 1; y <= 3; y++)
            world.SetCell(12, y, new() { Element = "Vacuum", Mass = 0, Temperature = 300 });
        AdvanceUntil("partition-open", frame => frame.RightWaterMass > 3000, phaseLimit);
        var flowed = frames[^1];
        checks.Add(new("Water enters the right-hand chamber", flowed.RightWaterMass > 100,
            flowed.RightWaterMass, "> 100 kg"));
        var beforeHeat = world.Snapshot();
        int target = Enumerable.Range(0, beforeHeat.Mass.Length)
            .Where(i => beforeHeat.Elements[i] == water && beforeHeat.Mass[i] > 1)
            .OrderByDescending(i => beforeHeat.Mass[i]).First();
        const float addedEnergy = 80000;
        events.Add(new(world.StepsCompleted, $"Add {addedEnergy} kJ to water at ({target % scenario.Width},{target / scenario.Width})", addedEnergy));
        world.AddEnergy(target % scenario.Width, target / scenario.Width, addedEnergy);
        Advance(50, "heated");
        var final = frames[^1];
        checks.Add(new("Water gets warmer after energy input", final.WaterMeanTemperature > flowed.WaterMeanTemperature + 0.05,
            final.WaterMeanTemperature, $"> {flowed.WaterMeanTemperature + 0.05:F3} K"));
        checks.Add(new("Water mass is conserved across all actions", Math.Abs(final.WaterMass - initial.WaterMass) <= 0.5,
            final.WaterMass, $"{initial.WaterMass:F3} +/- 0.5 kg"));
        checks.Add(new("Only the three removed solid cells change total mass", Math.Abs(final.TotalMass - (initial.TotalMass - 3000)) <= 0.5,
            final.TotalMass, $"{initial.TotalMass - 3000:F3} +/- 0.5 kg"));
        Persist();
        Check.That(checks.All(c => c.Passed), "Physics playground checks failed; trace.json retains the observed state.");

        void Persist()
        {
            var game = world.Installation;
            var trace = new
            {
                schemaVersion = 1,
                evidence = "native-sim-fixture",
                scenario.Name, scenario.Width, scenario.Height, scenario.Seed, scenario.SecondsPerStep,
                game.Adapter, game.NativeSha256, game.ManagedSha256,
                stepsCompleted = world.StepsCompleted,
                maximumPhaseSteps = phaseLimit,
                elapsedMilliseconds = watch.Elapsed.TotalMilliseconds,
                finalFingerprint = frames[^1].State.Fingerprint(),
                passed = checks.All(c => c.Passed),
                fixtureInputs = scenario,
                elements = scenario.Elements.Select((e, i) => new { index = i, e.Name, e.State }),
                events, checks, frames,
                limitation = "Controlled physics fixture, not a colony save or duplicant gameplay. Material transitions, diseases and radiation are disabled."
            };
            Directory.CreateDirectory(output);
            File.WriteAllText(Path.Combine(output, "trace.json"), JsonSerializer.Serialize(trace, HarnessJson.Options));
            foreach (var check in checks)
                Console.WriteLine($"[{(check.Passed ? "PASS" : "FAIL")}] {check.Name}: {check.Observed:F4}; expected {check.Expected}");
        }

        void Capture(string stage)
        {
            var state = world.Snapshot();
            state.ValidateFinite();
            double mass = 0, weightedY = 0, right = 0, weightedTemperature = 0;
            for (int i = 0; i < state.Mass.Length; i++)
            {
                if (state.Elements[i] != water) continue;
                mass += state.Mass[i];
                weightedY += state.Mass[i] * (i / state.Width);
                weightedTemperature += state.Mass[i] * state.Temperature[i];
                if (i % state.Width > 12) right += state.Mass[i];
            }
            Check.That(mass > 0, "No water remains in the fixture.");
            frames.Add(new(world.StepsCompleted, world.StepsCompleted * scenario.SecondsPerStep, stage,
                mass, weightedY / mass, right, weightedTemperature / mass, state.TotalMass, state));
        }

        void Advance(int steps, string stage)
        {
            for (int i = 0; i < steps; i += 5) { world.Step(5); Capture(stage); }
        }

        void AdvanceUntil(string stage, Func<RecordedFrame, bool> reached, int maximumSteps)
        {
            for (int i = 0; i < maximumSteps && !reached(frames[^1]); i += 20)
            {
                world.Step(20);
                Capture(stage);
            }
            bool arrived = reached(frames[^1]);
            checks.Add(new($"Observed target state before leaving {stage}", arrived, world.StepsCompleted,
                $"Target reached within {maximumSteps} steps for this phase"));
            if (!arrived)
            {
                Persist();
                throw new TimeoutException($"The {stage} target was not reached; no subsequent action was issued.");
            }
        }
    }
}

public sealed record RecordedEvent(int Step, string Action, float? AddedEnergyKj);
public sealed record RecordedCheck(string Name, bool Passed, double Observed, string Expected);
public sealed record RecordedFrame(int Step, float Seconds, string Stage, double WaterMass, double WaterCentreY,
    double RightWaterMass, double WaterMeanTemperature, double TotalMass, Snapshot State);
