using System.Reflection;
using System.Reflection.Metadata;
using System.Reflection.Metadata.Ecma335;
using System.Reflection.PortableExecutable;
using System.Runtime.Loader;

namespace OniHarness;

[AttributeUsage(AttributeTargets.Method)]
public sealed class HarnessTestAttribute : Attribute;

public sealed class HarnessContext(string? gamePath)
{
    public string? GamePath { get; } = gamePath;
    public GameInstallation? NativeInstallation { get; private set; }
    public GameInstallation? InvokedGameInstallation { get; private set; }
    public GameInstallation Game => GameInstallation.Inspect(GamePath ?? throw new InvalidOperationException("This test needs --game-path."));
    public NativeWorld OpenWorld(Scenario scenario)
    {
        var world = new NativeWorld(Game, scenario);
        NativeInstallation = world.Installation;
        return world;
    }

    /// <summary>Invokes actual installed managed code in the .NET host. Unity internal calls remain unavailable.</summary>
    public object? InvokeGameStatic(string type, string method, params object[] arguments)
    {
        var game = Game;
        var scope = new HarnessAssemblyScope(game.ManagedPath);
        var assembly = scope.LoadFromAssemblyPath(game.ManagedPath);
        var targetType = assembly.GetType(type, throwOnError: true)!;
        var target = targetType.GetMethod(method, BindingFlags.Public | BindingFlags.NonPublic | BindingFlags.Static,
            binder: null, types: arguments.Select(a => a.GetType()).ToArray(), modifiers: null)
            ?? throw new MissingMethodException(type, method);
        object? result = target.Invoke(null, arguments);
        InvokedGameInstallation = game;
        return result;
    }
}

public sealed record ManagedTestCase(string Name, int Token);

public static class ManagedTests
{
    /// <summary>Discovers attributes from metadata without executing the test assembly in the parent process.</summary>
    public static ManagedTestCase[] Discover(string assemblyPath)
    {
        using var stream = File.OpenRead(assemblyPath);
        using var pe = new PEReader(stream);
        var metadata = pe.GetMetadataReader();
        var tests = new List<ManagedTestCase>();
        foreach (var typeHandle in metadata.TypeDefinitions)
        {
            var type = metadata.GetTypeDefinition(typeHandle);
            foreach (var methodHandle in type.GetMethods())
            {
                var method = metadata.GetMethodDefinition(methodHandle);
                foreach (var attributeHandle in method.GetCustomAttributes())
                {
                    var attribute = metadata.GetCustomAttribute(attributeHandle);
                    if (attribute.Constructor.Kind != HandleKind.MemberReference) continue;
                    var ctor = metadata.GetMemberReference((MemberReferenceHandle)attribute.Constructor);
                    if (ctor.Parent.Kind != HandleKind.TypeReference) continue;
                    var reference = metadata.GetTypeReference((TypeReferenceHandle)ctor.Parent);
                    if (metadata.GetString(reference.Namespace) != "OniHarness" || metadata.GetString(reference.Name) != "HarnessTestAttribute") continue;
                    string ns = metadata.GetString(type.Namespace);
                    string name = (ns.Length == 0 ? "" : ns + ".") + metadata.GetString(type.Name) + "." + metadata.GetString(method.Name);
                    tests.Add(new(name, MetadataTokens.GetToken(methodHandle)));
                }
            }
        }
        return tests.ToArray();
    }

    public static async Task ExecuteAsync(string assemblyPath, int token, HarnessContext context)
    {
        var scope = new HarnessAssemblyScope(assemblyPath);
        var assembly = scope.LoadFromAssemblyPath(Path.GetFullPath(assemblyPath));
        var method = assembly.ManifestModule.ResolveMethod(token) as MethodInfo
            ?? throw new InvalidDataException("Test token is not a method.");
        Check.That(method.IsPublic && method.IsStatic && !method.ContainsGenericParameters && method.IsDefined(typeof(HarnessTestAttribute)),
            "Harness tests must be public static, non-generic methods with [HarnessTest].");
        var parameters = method.GetParameters();
        Check.That(parameters.Length == 0 || (parameters.Length == 1 && parameters[0].ParameterType == typeof(HarnessContext)),
            "A test accepts no arguments or one HarnessContext.");
        Check.That(method.ReturnType == typeof(void) || method.ReturnType == typeof(Task), "A test must return void or Task; async void is unsupported.");
        Check.That(!method.IsDefined(typeof(System.Runtime.CompilerServices.AsyncStateMachineAttribute)) || method.ReturnType == typeof(Task), "async void tests are unsupported.");
        object? result = method.Invoke(null, parameters.Length == 0 ? null : [context]);
        if (result is Task task) await task;
    }
}

internal sealed class HarnessAssemblyScope(string mainPath) : AssemblyLoadContext
{
    private static readonly HashSet<string> PlatformAssemblies =
        ((string?)AppContext.GetData("TRUSTED_PLATFORM_ASSEMBLIES") ?? "").Split(Path.PathSeparator)
        .Select(p => Path.GetFileNameWithoutExtension(p)!).ToHashSet(StringComparer.OrdinalIgnoreCase);
    private readonly string directory = Path.GetDirectoryName(Path.GetFullPath(mainPath))!;
    private readonly AssemblyDependencyResolver resolver = new(Path.GetFullPath(mainPath));

    protected override Assembly? Load(AssemblyName name)
    {
        if (name.Name == typeof(HarnessContext).Assembly.GetName().Name) return typeof(HarnessContext).Assembly;
        // Game installations include Unity's framework DLLs; use the host's framework instead.
        if (name.Name != null && PlatformAssemblies.Contains(name.Name)) return null;
        string? resolved = resolver.ResolveAssemblyToPath(name);
        string candidate = Path.Combine(directory, name.Name + ".dll");
        return resolved != null ? LoadFromAssemblyPath(resolved) : File.Exists(candidate) ? LoadFromAssemblyPath(candidate) : null;
    }
}
