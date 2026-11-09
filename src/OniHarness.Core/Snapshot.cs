using System.Security.Cryptography;

namespace OniHarness;

public sealed record Snapshot(int Width, int Height, ushort[] Elements, float[] Mass, float[] Temperature)
{
    public double TotalMass => Mass.Sum(x => (double)x);
    public int Index(int x, int y)
    {
        Check.That(x >= 0 && y >= 0 && x < Width && y < Height, "Cell coordinates outside the world.");
        return y * Width + x;
    }

    public string Fingerprint()
    {
        using var bytes = new MemoryStream();
        using var writer = new BinaryWriter(bytes);
        writer.Write(Width);
        writer.Write(Height);
        for (int i = 0; i < Mass.Length; i++)
        {
            writer.Write(Elements[i]);
            writer.Write(Mass[i]);
            writer.Write(Temperature[i]);
        }
        return Convert.ToHexStringLower(SHA256.HashData(bytes.ToArray()));
    }

    public void ValidateFinite() => Check.That(Mass.All(x => float.IsFinite(x) && x >= 0) &&
        Temperature.All(x => float.IsFinite(x) && x >= 0), "Simulation produced invalid mass or temperature.");

    public double RegionMass(int x, int y, int width, int height, ushort? elementIndex = null)
    {
        double sum = 0;
        int maxX = Math.Min(x + width, Width);
        int maxY = Math.Min(y + height, Height);
        for (int cy = Math.Max(0, y); cy < maxY; cy++)
        {
            for (int cx = Math.Max(0, x); cx < maxX; cx++)
            {
                int idx = Index(cx, cy);
                if (!elementIndex.HasValue || Elements[idx] == elementIndex.Value)
                    sum += Mass[idx];
            }
        }
        return sum;
    }

    public int ColumnLiquidLevel(int x, ushort? liquidIndex = null, float minMass = 1.0f)
    {
        Check.That(x >= 0 && x < Width, "Column x outside the world.");
        for (int y = Height - 1; y >= 0; y--)
        {
            int idx = Index(x, y);
            if (Mass[idx] >= minMass && (!liquidIndex.HasValue || Elements[idx] == liquidIndex.Value))
                return y;
        }
        return -1;
    }
}

public sealed record Expectation
{
    public required string Kind { get; init; }
    public int X { get; init; }
    public int Y { get; init; }
    public int X2 { get; init; }
    public int Y2 { get; init; }
    public int Width { get; init; } = 1;
    public int Height { get; init; } = 1;
    public double? Min { get; init; }
    public double? Max { get; init; }
    public double AbsoluteTolerance { get; init; } = 0.01;

    public void Validate(int width, int height)
    {
        Check.That(Kind is "massConserved" or "totalMass" or "cellMass" or "cellTemperature"
            or "regionMass" or "columnLiquidLevel" or "communicatingVesselsEquilibrium" or "columnHeightNear",
            $"Unknown assertion kind: {Kind}");
        Check.That(double.IsFinite(AbsoluteTolerance) && AbsoluteTolerance >= 0, "Invalid assertion tolerance.");
        if (Kind != "massConserved" && Kind != "communicatingVesselsEquilibrium" && Kind != "columnHeightNear")
        {
            Check.That(Min.HasValue || Max.HasValue, "A range assertion needs min or max.");
            Check.That((Min == null || double.IsFinite(Min.Value)) && (Max == null || double.IsFinite(Max.Value)) &&
                !(Min > Max), "Invalid assertion range.");
        }
        if (Kind.StartsWith("cell", StringComparison.Ordinal) || Kind is "columnLiquidLevel")
            Check.That(X >= 0 && Y >= 0 && X < width && Y < height, "Assertion cell is outside the world.");
        if (Kind is "communicatingVesselsEquilibrium" or "columnHeightNear")
        {
            Check.That(X >= 0 && X < width && X2 >= 0 && X2 < width, "Communicating columns outside the world.");
        }
    }

    public void Verify(Snapshot initial, Snapshot final)
    {
        if (Kind == "massConserved")
        {
            Check.Near(initial.TotalMass, final.TotalMass, AbsoluteTolerance, "Mass conservation failed");
            return;
        }
        if (Kind is "communicatingVesselsEquilibrium" or "columnHeightNear")
        {
            int h1 = final.ColumnLiquidLevel(X);
            int h2 = final.ColumnLiquidLevel(X2);
            double diff = Math.Abs(h1 - h2);
            Check.That(diff <= AbsoluteTolerance,
                $"Communicating vessels equilibrium failed between column {X} (h={h1}) and column {X2} (h={h2}); diff={diff} exceeds tolerance {AbsoluteTolerance}");
            return;
        }
        double value = Kind switch
        {
            "totalMass" => final.TotalMass,
            "cellMass" => final.Mass[final.Index(X, Y)],
            "cellTemperature" => final.Temperature[final.Index(X, Y)],
            "regionMass" => final.RegionMass(X, Y, Width, Height),
            "columnLiquidLevel" => final.ColumnLiquidLevel(X),
            _ => throw new InvalidDataException($"Unknown assertion kind: {Kind}")
        };
        Check.That(!(value < Min) && !(value > Max), $"{Kind} = {value}, outside [{Min}, {Max}].");
    }
}
