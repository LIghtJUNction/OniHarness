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
}

public sealed record Expectation
{
    public required string Kind { get; init; }
    public int X { get; init; }
    public int Y { get; init; }
    public double? Min { get; init; }
    public double? Max { get; init; }
    public double AbsoluteTolerance { get; init; } = 0.01;

    public void Validate(int width, int height)
    {
        Check.That(Kind is "massConserved" or "totalMass" or "cellMass" or "cellTemperature", $"Unknown assertion kind: {Kind}");
        Check.That(double.IsFinite(AbsoluteTolerance) && AbsoluteTolerance >= 0, "Invalid assertion tolerance.");
        if (Kind != "massConserved")
        {
            Check.That(Min.HasValue || Max.HasValue, "A range assertion needs min or max.");
            Check.That((Min == null || double.IsFinite(Min.Value)) && (Max == null || double.IsFinite(Max.Value)) &&
                !(Min > Max), "Invalid assertion range.");
        }
        if (Kind.StartsWith("cell", StringComparison.Ordinal))
            Check.That(X >= 0 && Y >= 0 && X < width && Y < height, "Assertion cell is outside the world.");
    }

    public void Verify(Snapshot initial, Snapshot final)
    {
        if (Kind == "massConserved")
        {
            Check.Near(initial.TotalMass, final.TotalMass, AbsoluteTolerance, "Mass conservation failed");
            return;
        }
        double value = Kind switch
        {
            "totalMass" => final.TotalMass,
            "cellMass" => final.Mass[final.Index(X, Y)],
            "cellTemperature" => final.Temperature[final.Index(X, Y)],
            _ => throw new InvalidDataException($"Unknown assertion kind: {Kind}")
        };
        Check.That(!(value < Min) && !(value > Max), $"{Kind} ({X},{Y}) = {value}, outside [{Min}, {Max}].");
    }
}
