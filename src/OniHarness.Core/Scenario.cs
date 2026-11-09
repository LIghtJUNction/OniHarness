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

public sealed record Point2D
{
    public int X { get; init; }
    public int Y { get; init; }

    public Point2D() { }
    public Point2D(int x, int y) { X = x; Y = y; }
}

public sealed record Region
{
    public int X { get; init; }
    public int Y { get; init; }
    public int Width { get; init; } = 1;
    public int Height { get; init; } = 1;
    public CellInput Cell { get; init; } = new();
}

public sealed record PointsRegion
{
    public Point2D[] Points { get; init; } = [];
    public CellInput Cell { get; init; } = new();
}

public sealed record PolygonRegion
{
    public Point2D[] Vertices { get; init; } = [];
    public CellInput Cell { get; init; } = new();

    public static bool ContainsPoint(Point2D[] vertices, int x, int y)
    {
        if (vertices.Length < 3) return false;
        bool inside = false;
        for (int i = 0, j = vertices.Length - 1; i < vertices.Length; j = i++)
        {
            double xi = vertices[i].X, yi = vertices[i].Y;
            double xj = vertices[j].X, yj = vertices[j].Y;
            bool intersect = ((yi > y) != (yj > y)) &&
                (x < (xj - xi) * (y - yi) / (yj - yi) + xi);
            if (intersect) inside = !inside;
        }
        return inside;
    }
}

public sealed record BuildingPlacement
{
    public required string BuildingDef { get; init; }
    public int X { get; init; }
    public int Y { get; init; }
    public string Element { get; init; } = "Granite";
    public float Temperature { get; init; } = 300f;
    public float Mass { get; init; } = 200f;
    public bool IsOpen { get; init; } = false;
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
    public PointsRegion[] PointsRegions { get; init; } = [];
    public PolygonRegion[] PolygonRegions { get; init; } = [];
    public string[] AsciiMap { get; init; } = [];
    public Dictionary<string, CellInput> Legend { get; init; } = [];
    public int AsciiOriginX { get; init; } = 0;
    public int AsciiOriginY { get; init; } = 0;
    public BuildingPlacement[] Buildings { get; init; } = [];
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
        Check.That(PointsRegions != null && PolygonRegions != null && AsciiMap != null && Legend != null && Buildings != null,
            "Geometry collections cannot be null.");
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
        foreach (var points in PointsRegions)
        {
            Check.That(points != null && points.Points != null, "PointsRegion cannot be null.");
            foreach (var pt in points.Points)
            {
                Check.That(pt != null && pt.X >= 0 && pt.Y >= 0 && pt.X < Width && pt.Y < Height, $"Point ({pt?.X},{pt?.Y}) is outside the world.");
            }
            ValidateCell(points.Cell);
        }
        foreach (var poly in PolygonRegions)
        {
            Check.That(poly != null && poly.Vertices != null && poly.Vertices.Length >= 3, "PolygonRegion requires at least 3 vertices.");
            foreach (var v in poly.Vertices)
            {
                Check.That(v != null && v.X >= 0 && v.Y >= 0 && v.X < Width && v.Y < Height, $"Vertex ({v?.X},{v?.Y}) is outside the world.");
            }
            ValidateCell(poly.Cell);
        }
        if (AsciiMap.Length > 0)
        {
            Check.That(AsciiMap.Length <= Height, "AsciiMap row count exceeds world height.");
            for (int r = 0; r < AsciiMap.Length; r++)
            {
                string row = AsciiMap[r];
                Check.That(row.Length <= Width, $"AsciiMap row {r} length exceeds world width.");
                foreach (char c in row)
                {
                    string key = c.ToString();
                    Check.That(Legend.ContainsKey(key), $"AsciiMap character '{key}' not found in Legend.");
                }
            }
            foreach (var entry in Legend)
            {
                ValidateCell(entry.Value);
            }
        }
        foreach (var b in Buildings)
        {
            Check.That(b != null && !string.IsNullOrWhiteSpace(b.BuildingDef), "BuildingDef cannot be empty.");
            Check.That(b.X >= 0 && b.Y >= 0 && b.X < Width && b.Y < Height, $"Building ({b.X},{b.Y}) is outside the world.");
            Check.That(float.IsFinite(b.Mass) && b.Mass >= 0, "Building mass must be finite and nonnegative.");
            Check.That(float.IsFinite(b.Temperature) && b.Temperature > 0, "Building temperature must be positive.");
            ElementIndex(b.Element);
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
