using System.Reflection.Metadata;
using System.Reflection.PortableExecutable;
using System.Runtime.InteropServices;
using System.Security.Cryptography;

namespace OniHarness;

public sealed record GameInstallation(string Root, string NativePath, string ManagedPath, string NativeSha256, string ManagedSha256, string Adapter)
{
    public const string VerifiedNativeSha256 = "85111cd0b1fc14c79c785581d6c055280b9d48ba705776f3d14eb0a7b239c12d";
    public const string VerifiedManagedSha256 = "6db652990ad1038e86b3493ed43004e12bfd61e62a4be60c787a9a9b9ac52ee1";
    public const string VerifiedAdapter = "oni-744825-linux-x64-v1";

    public static GameInstallation Inspect(string root)
    {
        root = Path.GetFullPath(root);
        string data = Path.Combine(root, "OxygenNotIncluded_Data");
        string native = Path.Combine(data, "Plugins", "libSimDLL.so");
        string managed = Path.Combine(data, "Managed", "Assembly-CSharp.dll");
        if (!File.Exists(native) || !File.Exists(managed))
            throw new FileNotFoundException("Expected a Linux ONI installation containing Plugins/libSimDLL.so and Managed/Assembly-CSharp.dll.");
        string nh = Hash(native), mh = Hash(managed);
        bool supported = OperatingSystem.IsLinux() && RuntimeInformation.ProcessArchitecture == Architecture.X64 &&
            nh == VerifiedNativeSha256 && mh == VerifiedManagedSha256;
        return new(root, native, managed, nh, mh, supported ? VerifiedAdapter : "unsupported");
    }

    public void RequireSupported()
    {
        if (Adapter != VerifiedAdapter)
            throw new NotSupportedException($"No verified native adapter for this installation ({NativeSha256}). Run doctor; do not substitute an older DLL.");
    }

    public static string Hash(string path)
    {
        using var file = File.OpenRead(path);
        return Convert.ToHexStringLower(SHA256.HashData(file));
    }
}

public sealed record MethodContract(string Type, string Method, int Parameters);

public static class AssemblyMetadata
{
    public static bool HasMethod(string path, MethodContract contract)
    {
        using var stream = File.OpenRead(path);
        using var pe = new PEReader(stream);
        var reader = pe.GetMetadataReader();
        foreach (var handle in reader.TypeDefinitions)
        {
            var type = reader.GetTypeDefinition(handle);
            if (FullName(reader, type) != contract.Type) continue;
            foreach (var methodHandle in type.GetMethods())
            {
                var method = reader.GetMethodDefinition(methodHandle);
                if (reader.GetString(method.Name) != contract.Method) continue;
                var blob = reader.GetBlobReader(method.Signature);
                var header = blob.ReadSignatureHeader();
                if (header.IsGeneric) blob.ReadCompressedInteger();
                if (blob.ReadCompressedInteger() == contract.Parameters) return true;
            }
        }
        return false;
    }

    private static string FullName(MetadataReader reader, TypeDefinition type)
    {
        string name = reader.GetString(type.Name);
        if (!type.GetDeclaringType().IsNil)
            return FullName(reader, reader.GetTypeDefinition(type.GetDeclaringType())) + "+" + name;
        string ns = reader.GetString(type.Namespace);
        return ns.Length == 0 ? name : ns + "." + name;
    }
}
