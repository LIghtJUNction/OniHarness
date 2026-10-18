using System.Text.Json;
using System.Text.Json.Serialization;

namespace OniHarness;

public static class HarnessJson
{
    public static JsonSerializerOptions Options { get; } = new()
    {
        PropertyNamingPolicy = JsonNamingPolicy.CamelCase,
        PropertyNameCaseInsensitive = false,
        WriteIndented = true,
        UnmappedMemberHandling = JsonUnmappedMemberHandling.Disallow,
        Converters = { new JsonStringEnumConverter(JsonNamingPolicy.CamelCase, false) }
    };

    public static T Read<T>(string path) => JsonSerializer.Deserialize<T>(File.ReadAllText(path), Options)
        ?? throw new InvalidDataException($"Empty JSON document: {path}");
}

public enum ElementState : byte { Vacuum = 0, Gas = 1, Liquid = 2, Solid = 3, Unbreakable = 7 }

/// <summary>Explicit fixture inputs, not a copy of the live game's element database.</summary>
public sealed record ElementDefinition
{
    public required string Name { get; init; }
    public required int Id { get; init; }
    public ElementState State { get; init; }
    public float SpecificHeatCapacity { get; init; } = 1;
    public float ThermalConductivity { get; init; }
    public float MolarMass { get; init; } = 32;
    public float MaxMass { get; init; } = 20000;
    public float Viscosity { get; init; } = 1;
    public float Flow { get; init; } = 0.1f;

    public static ElementDefinition[] Fixtures() =>
    [
        new() { Name = "Vacuum", Id = 758759285, State = ElementState.Vacuum, MaxMass = 0 },
        new() { Name = "Granite", Id = -105943486, State = ElementState.Solid,
            SpecificHeatCapacity = 0.79f, ThermalConductivity = 3.39f },
        new() { Name = "Oxygen", Id = -1528777920, State = ElementState.Gas,
            SpecificHeatCapacity = 1.005f, ThermalConductivity = 0.024f },
        new() { Name = "Water", Id = 1836671383, State = ElementState.Liquid,
            SpecificHeatCapacity = 4.179f, ThermalConductivity = 0.609f },
        new() { Name = "Unobtanium", Id = 1838482828, State = ElementState.Unbreakable },
        new() { Name = "Void", Id = -1456075980, State = ElementState.Vacuum, MaxMass = 0 }
    ];
}

public sealed record CellInput
{
    public string Element { get; init; } = "Oxygen";
    public float Mass { get; init; } = 1;
    public float Temperature { get; init; } = 300;
}

public sealed record Region
{
    public int X { get; init; }
    public int Y { get; init; }
    public int Width { get; init; } = 1;
    public int Height { get; init; } = 1;
    public CellInput Cell { get; init; } = new();
}

public sealed record Scenario
{
    public int SchemaVersion { get; init; } = 1;
    public string Name { get; init; } = "Unnamed scenario";
    public int Width { get; init; } = 16;
    public int Height { get; init; } = 16;
    public uint Seed { get; init; } = 42;
    public int Steps { get; init; } = 50;
    public float SecondsPerStep { get; init; } = 0.2f;
    public ElementDefinition[] Elements { get; init; } = ElementDefinition.Fixtures();
    public CellInput Fill { get; init; } = new();
    public CellInput Boundary { get; init; } = new() { Element = "Unobtanium", Mass = 20000 };
    public Region[] Regions { get; init; } = [];
    public Expectation[] Assertions { get; init; } = [];

    public void Validate()
    {
        Check.That(SchemaVersion == 1, "Only scenario schemaVersion 1 is supported.");
        Check.That(!string.IsNullOrWhiteSpace(Name), "A scenario needs a name.");
        Check.That(Width is >= 16 and <= 512 && Height is >= 16 and <= 512, "World dimensions must be 16..512.");
        Check.That(Steps is >= 0 and <= 100000, "Steps must be 0..100000.");
        Check.That(float.IsFinite(SecondsPerStep) && SecondsPerStep is > 0 and <= 0.2f, "secondsPerStep must be > 0 and <= 0.2.");
        Check.That(Elements is { Length: >= 2 and <= 1024 }, "Provide 2..1024 fixture elements.");
        Check.That(Fill != null && Boundary != null && Regions != null && Assertions != null, "Scenario collections and cell inputs cannot be null.");
        var names = new HashSet<string>(StringComparer.Ordinal);
        var ids = new HashSet<int>();
        foreach (var e in Elements)
        {
            Check.That(e != null && !string.IsNullOrWhiteSpace(e.Name) && names.Add(e.Name) && ids.Add(e.Id), "Element names and IDs must be unique.");
            Check.That(Enum.IsDefined(e.State), $"Unknown state for {e.Name}.");
            Check.That(float.IsFinite(e.SpecificHeatCapacity) && e.SpecificHeatCapacity > 0 &&
                float.IsFinite(e.ThermalConductivity) && e.ThermalConductivity >= 0 &&
                float.IsFinite(e.MolarMass) && e.MolarMass > 0 && float.IsFinite(e.MaxMass) && e.MaxMass >= 0 &&
                float.IsFinite(e.Viscosity) && e.Viscosity > 0 && float.IsFinite(e.Flow) && e.Flow >= 0,
                $"Invalid physical input for {e.Name}.");
        }
        foreach (int required in new[] { 758759285, -1456075980, 1838482828 })
            Check.That(ids.Contains(required), $"The native protocol requires element ID {required}.");
        Check.That(Elements.Single(e => e.Id == 758759285).State == ElementState.Vacuum &&
            Elements.Single(e => e.Id == -1456075980).State == ElementState.Vacuum &&
            Elements.Single(e => e.Id == 1838482828).State == ElementState.Unbreakable,
            "Vacuum, Void and Unobtanium require their native states.");
        ValidateCell(Fill);
        ValidateCell(Boundary);
        foreach (var region in Regions)
        {
            Check.That(region != null && region.Width > 0 && region.Height > 0 && region.X >= 0 && region.Y >= 0 &&
                region.X <= Width - region.Width && region.Y <= Height - region.Height, "Region is outside the world.");
            ValidateCell(region.Cell);
        }
        foreach (var assertion in Assertions) assertion.Validate(Width, Height);
    }

    public ushort ElementIndex(string name)
    {
        int index = Array.FindIndex(Elements, e => e.Name == name);
        return index < 0 ? throw new InvalidDataException($"Unknown fixture element: {name}") : checked((ushort)index);
    }

    public void ValidateCell(CellInput cell)
    {
        Check.That(cell != null, "Cell input cannot be null.");
        var element = Elements[ElementIndex(cell.Element)];
        Check.That(float.IsFinite(cell.Mass) && cell.Mass >= 0 && cell.Mass <= element.MaxMass &&
            float.IsFinite(cell.Temperature) && cell.Temperature is > 0 and <= 10000,
            $"Invalid mass/temperature for {cell.Element}.");
        Check.That(element.State != ElementState.Vacuum || cell.Mass == 0, "Vacuum must have zero mass.");
    }
}

public static class Check
{
    public static void That([System.Diagnostics.CodeAnalysis.DoesNotReturnIf(false)] bool condition, string message)
    {
        if (!condition) throw new InvalidDataException(message);
    }

    public static void Near(double expected, double actual, double tolerance, string message = "Values differ") =>
        That(double.IsFinite(expected) && double.IsFinite(actual) && double.IsFinite(tolerance) && tolerance >= 0 &&
            Math.Abs(expected - actual) <= tolerance, $"{message}: expected {expected}, actual {actual}, tolerance {tolerance}.");
}
