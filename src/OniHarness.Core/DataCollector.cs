using System.Globalization;
using System.Text;
using System.Text.Json;

namespace OniHarness;

public sealed record ProbePoint(string Name, int X, int Y);

public sealed record TelemetrySample
{
    public int Step { get; init; }
    public double TimeSeconds { get; init; }
    public double TotalMass { get; init; }
    public Dictionary<string, double> PointMass { get; init; } = [];
    public Dictionary<string, double> PointTemperature { get; init; } = [];
    public Dictionary<string, int> ColumnLiquidLevel { get; init; } = [];
    public Dictionary<string, double> RegionMass { get; init; } = [];
}

public sealed class SimulationDataCollector(Scenario scenario)
{
    public Scenario Scenario { get; } = scenario;
    public List<TelemetrySample> Samples { get; } = [];
    public List<ProbePoint> Probes { get; } = [];
    public Dictionary<string, int> MonitoredColumns { get; } = [];
    public Dictionary<string, (int X, int Y, int Width, int Height)> MonitoredRegions { get; } = [];

    public void AddProbe(string name, int x, int y)
    {
        Check.That(x >= 0 && x < Scenario.Width && y >= 0 && y < Scenario.Height, "Probe outside scenario world.");
        Probes.Add(new(name, x, y));
    }

    public void MonitorColumn(string name, int x)
    {
        Check.That(x >= 0 && x < Scenario.Width, "Monitored column outside scenario world.");
        MonitoredColumns[name] = x;
    }

    public void MonitorRegion(string name, int x, int y, int width, int height)
    {
        Check.That(x >= 0 && y >= 0 && x + width <= Scenario.Width && y + height <= Scenario.Height, "Monitored region outside world.");
        MonitoredRegions[name] = (x, y, width, height);
    }

    public TelemetrySample Record(int step, Snapshot snapshot)
    {
        var sample = new TelemetrySample
        {
            Step = step,
            TimeSeconds = step * Scenario.SecondsPerStep,
            TotalMass = snapshot.TotalMass,
            PointMass = Probes.ToDictionary(p => p.Name, p => (double)snapshot.Mass[snapshot.Index(p.X, p.Y)]),
            PointTemperature = Probes.ToDictionary(p => p.Name, p => (double)snapshot.Temperature[snapshot.Index(p.X, p.Y)]),
            ColumnLiquidLevel = MonitoredColumns.ToDictionary(c => c.Key, c => snapshot.ColumnLiquidLevel(c.Value)),
            RegionMass = MonitoredRegions.ToDictionary(r => r.Key, r => snapshot.RegionMass(r.Value.X, r.Value.Y, r.Value.Width, r.Value.Height))
        };
        Samples.Add(sample);
        return sample;
    }

    public string ToCsv()
    {
        var sb = new StringBuilder();
        var headers = new List<string> { "Step", "TimeSeconds", "TotalMass" };
        foreach (var p in Probes) headers.Add($"Mass_{p.Name}");
        foreach (var p in Probes) headers.Add($"Temp_{p.Name}");
        foreach (var c in MonitoredColumns.Keys) headers.Add($"Level_{c}");
        foreach (var r in MonitoredRegions.Keys) headers.Add($"Region_{r}");
        sb.AppendLine(string.Join(",", headers));

        foreach (var s in Samples)
        {
            var row = new List<string>
            {
                s.Step.ToString(CultureInfo.InvariantCulture),
                s.TimeSeconds.ToString("F3", CultureInfo.InvariantCulture),
                s.TotalMass.ToString("F3", CultureInfo.InvariantCulture)
            };
            foreach (var p in Probes)
                row.Add(s.PointMass.TryGetValue(p.Name, out double m) ? m.ToString("F3", CultureInfo.InvariantCulture) : "0");
            foreach (var p in Probes)
                row.Add(s.PointTemperature.TryGetValue(p.Name, out double t) ? t.ToString("F3", CultureInfo.InvariantCulture) : "0");
            foreach (var c in MonitoredColumns.Keys)
                row.Add(s.ColumnLiquidLevel.TryGetValue(c, out int lvl) ? lvl.ToString(CultureInfo.InvariantCulture) : "-1");
            foreach (var r in MonitoredRegions.Keys)
                row.Add(s.RegionMass.TryGetValue(r, out double rm) ? rm.ToString("F3", CultureInfo.InvariantCulture) : "0");
            sb.AppendLine(string.Join(",", row));
        }
        return sb.ToString();
    }

    public void SaveCsv(string path)
    {
        Directory.CreateDirectory(Path.GetDirectoryName(Path.GetFullPath(path))!);
        File.WriteAllText(path, ToCsv(), Encoding.UTF8);
    }

    public void SaveJson(string path)
    {
        Directory.CreateDirectory(Path.GetDirectoryName(Path.GetFullPath(path))!);
        File.WriteAllText(path, JsonSerializer.Serialize(Samples, HarnessJson.Options), Encoding.UTF8);
    }
}
