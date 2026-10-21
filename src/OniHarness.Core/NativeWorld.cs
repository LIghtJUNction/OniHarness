using System.Diagnostics;
using System.Runtime.InteropServices;

namespace OniHarness;

/// <summary>One native session per process. Use the CLI for crash/timeout isolation.</summary>
public sealed class NativeWorld : IDisposable
{
    [UnmanagedFunctionPointer(CallingConvention.Cdecl)] private delegate int Callback(int id, IntPtr data);
    [UnmanagedFunctionPointer(CallingConvention.Cdecl)] private delegate void Initialize(Callback callback);
    [UnmanagedFunctionPointer(CallingConvention.Cdecl)] private delegate void Shutdown();
    [UnmanagedFunctionPointer(CallingConvention.Cdecl)] private delegate IntPtr Message(int id, int length, IntPtr bytes);

    [StructLayout(LayoutKind.Sequential, Pack = 4)]
    private struct Update
    {
        public int Frames;
        public IntPtr Elements;
        public IntPtr Temperature;
        public IntPtr Mass;
    }

    private static int sessionUsed;
    private readonly Scenario scenario;
    private readonly IntPtr library;
    private readonly Callback callback;
    private readonly Shutdown shutdown;
    private readonly Message message;
    private readonly byte[] visible;
    private readonly int thread = Environment.CurrentManagedThreadId;
    private bool initialized;
    private bool disposed;
    private IntPtr update;
    private int callbackError;
    public int StepsCompleted { get; private set; }
    public GameInstallation Installation { get; }

    public NativeWorld(GameInstallation installation, Scenario scenario)
    {
        scenario.Validate();
        Installation = GameInstallation.Inspect(installation.Root);
        Installation.RequireSupported();
        Check.That(Interlocked.Exchange(ref sessionUsed, 1) == 0, "Only one native world is allowed per process. Split worlds into separate harness tests.");
        this.scenario = scenario;
        visible = Enumerable.Repeat((byte)255, scenario.Width * scenario.Height).ToArray();
        library = NativeLibrary.Load(Installation.NativePath);
        try
        {
            var initialize = Export<Initialize>("SIM_Initialize");
            shutdown = Export<Shutdown>("SIM_Shutdown");
            message = Export<Message>("SIM_HandleMessage");
            callback = (id, _) => { if (id is 0 or 1) Interlocked.Exchange(ref callbackError, 1); return 0; };
            initialize(callback);
            initialized = true;
            Send(1108437482, EncodeElements(scenario.Elements));
            Send(825301935, Encode(w => { w.Write(0); w.Write(scenario.Elements.Length); }));
            Send(2062421945, EncodeWorld(scenario));
            update = Send(-931446686, []);
            Check.That(update != IntPtr.Zero, "Native simulation did not return initial data.");
            Snapshot().ValidateFinite();
        }
        catch
        {
            if (initialized) shutdown!();
            NativeLibrary.Free(library);
            throw;
        }
    }

    public Snapshot Snapshot()
    {
        EnsureAlive();
        Check.That(update != IntPtr.Zero, "No native snapshot available.");
        var data = Marshal.PtrToStructure<Update>(update);
        Check.That(data.Elements != IntPtr.Zero && data.Mass != IntPtr.Zero && data.Temperature != IntPtr.Zero, "Native snapshot contains null arrays.");
        var indices = new short[visible.Length];
        var mass = new float[visible.Length];
        var temperature = new float[visible.Length];
        Marshal.Copy(data.Elements, indices, 0, indices.Length);
        Marshal.Copy(data.Mass, mass, 0, mass.Length);
        Marshal.Copy(data.Temperature, temperature, 0, temperature.Length);
        return new(scenario.Width, scenario.Height, Array.ConvertAll(indices, x => unchecked((ushort)x)), mass, temperature);
    }

    public void Step(int count, TimeSpan? perStepTimeout = null)
    {
        EnsureAlive();
        Check.That(count is >= 0 and <= 100000, "Step count must be 0..100000.");
        var timeout = perStepTimeout ?? TimeSpan.FromSeconds(5);
        Check.That(timeout > TimeSpan.Zero && timeout <= TimeSpan.FromMinutes(1), "Invalid per-step timeout.");
        byte[] frame = Encode(w =>
        {
            w.Write(scenario.SecondsPerStep);
            w.Write(0); w.Write(0); w.Write(scenario.Width); w.Write(scenario.Height - 1);
            w.Write(0f); w.Write(0f);
        });
        for (int i = 0; i < count; i++)
        {
            Send(-775326397, frame);
            var watch = Stopwatch.StartNew();
            while (true)
            {
                update = Send(1078620451, visible);
                Check.That(update != IntPtr.Zero, "Native simulation returned no frame data.");
                int processed = Marshal.ReadInt32(update);
                if (processed == 1) break;
                Check.That(processed == 0, $"Expected one completed frame, got {processed}.");
                if (watch.Elapsed >= timeout) throw new TimeoutException("Native frame did not complete.");
                Thread.Sleep(1);
            }
            StepsCompleted++;
        }
    }

    public void SetCell(int x, int y, CellInput cell)
    {
        EnsureAlive();
        Check.That(x >= 0 && y >= 0 && x < scenario.Width && y < scenario.Height, "Cell coordinates outside the world.");
        scenario.ValidateCell(cell);
        Send(-1252920804, Encode(w =>
        {
            w.Write(y * scenario.Width + x); w.Write(-1);
            w.Write(cell.Temperature); w.Write(cell.Mass); w.Write(0);
            w.Write(scenario.ElementIndex(cell.Element)); w.Write((byte)1); w.Write((byte)255);
            w.Write((byte)1); w.Write(new byte[3]);
        }));
    }

    public void AddEnergy(int x, int y, float kilojoules, float maxTemperature = 10000)
    {
        EnsureAlive();
        Check.That(x >= 0 && y >= 0 && x < scenario.Width && y < scenario.Height, "Cell coordinates outside the world.");
        Check.That(float.IsFinite(kilojoules) && float.IsFinite(maxTemperature) && maxTemperature is > 0 and <= 10000, "Invalid energy input.");
        Send(818320644, Encode(w => { w.Write(y * scenario.Width + x); w.Write(kilojoules); w.Write(maxTemperature); w.Write(1000); }));
    }

    public void Dispose()
    {
        if (disposed) return;
        Check.That(thread == Environment.CurrentManagedThreadId, "Dispose a native world on its creating thread.");
        disposed = true;
        try { if (initialized) shutdown(); }
        finally { NativeLibrary.Free(library); GC.KeepAlive(callback); }
    }

    private void EnsureAlive()
    {
        ObjectDisposedException.ThrowIf(disposed, this);
        Check.That(thread == Environment.CurrentManagedThreadId, "A native world must be used on its creating thread.");
        Check.That(Volatile.Read(ref callbackError) == 0, "The native simulation reported an exception.");
    }

    private T Export<T>(string name) where T : Delegate => Marshal.GetDelegateForFunctionPointer<T>(NativeLibrary.GetExport(library, name));

    private IntPtr Send(int id, byte[] data)
    {
        var pinned = GCHandle.Alloc(data, GCHandleType.Pinned);
        try
        {
            IntPtr result = message(id, data.Length, data.Length == 0 ? IntPtr.Zero : pinned.AddrOfPinnedObject());
            Check.That(Volatile.Read(ref callbackError) == 0, "The native simulation reported an exception.");
            return result;
        }
        finally { pinned.Free(); }
    }

    private static byte[] Encode(Action<BinaryWriter> write)
    {
        using var stream = new MemoryStream();
        using var writer = new BinaryWriter(stream);
        write(writer);
        return stream.ToArray();
    }

    private static byte[] EncodeElements(ElementDefinition[] elements) => Encode(w =>
    {
        w.Write(elements.Length);
        for (ushort i = 0; i < elements.Length; i++)
        {
            var e = elements[i];
            w.Write(e.Id); w.Write(ushort.MaxValue); w.Write(ushort.MaxValue); w.Write(i);
            w.Write((byte)e.State); w.Write((byte)0);
            foreach (float value in new[] { e.SpecificHeatCapacity, e.ThermalConductivity, e.MolarMass,
                25f, 1f, 1f, e.Flow, e.Viscosity, 0.01f, 0.01f, e.MaxMass, 0f, 10000f, 100f }) w.Write(value);
            w.Write(758759285); w.Write(0f); w.Write(758759285); w.Write(0f);
            w.Write(ushort.MaxValue); w.Write(ushort.MaxValue);
            w.Write(0u); w.Write(0u);
            for (int j = 0; j < 6; j++) w.Write(0u);
            w.Write(0);
            for (int j = 0; j < 7; j++) w.Write(0f);
            w.Write(300f); w.Write(0f); w.Write(1f);
        }
        foreach (var e in elements)
        {
            byte[] name = System.Text.Encoding.UTF8.GetBytes(e.Name);
            w.Write(name.Length); w.Write(name);
        }
    });

    private static byte[] EncodeWorld(Scenario s) => Encode(w =>
    {
        w.Write(s.Width); w.Write(s.Height); w.Write(s.Seed); w.Write(false); w.Write(true);
        var cells = Enumerable.Repeat(s.Fill, s.Width * s.Height).ToArray();
        for (int y = 0; y < s.Height; y++)
            for (int x = 0; x < s.Width; x++)
                if (x == 0 || y == 0 || x == s.Width - 1 || y == s.Height - 1) cells[y * s.Width + x] = s.Boundary;
        foreach (var region in s.Regions)
            for (int y = region.Y; y < region.Y + region.Height; y++)
                for (int x = region.X; x < region.X + region.Width; x++) cells[y * s.Width + x] = region.Cell;
        foreach (var cell in cells)
        {
            w.Write(s.ElementIndex(cell.Element)); w.Write((byte)0); w.Write((byte)255); w.Write(new byte[4]);
            w.Write(cell.Temperature); w.Write(cell.Mass);
        }
        foreach (var _ in cells) { w.Write((byte)255); w.Write(new byte[3]); w.Write(0); w.Write(0f); }
        ushort vacuum = s.ElementIndex(s.Elements.Single(e => e.Id == 758759285).Name);
        foreach (var _ in cells) { w.Write(vacuum); w.Write((ushort)0); w.Write(0f); w.Write(0f); }
    });
}
