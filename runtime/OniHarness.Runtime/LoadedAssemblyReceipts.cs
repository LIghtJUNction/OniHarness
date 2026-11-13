using System;
using System.Collections.Generic;
using System.IO;
using System.Reflection;
using System.Security.Cryptography;

namespace OniHarness.Runtime
{
    internal static class LoadedAssemblyReceipts
    {
        internal static bool Refresh(Dictionary<string, object> receipts)
        {
            bool changed = false;
            foreach (Assembly assembly in AppDomain.CurrentDomain.GetAssemblies())
            {
                if (assembly.IsDynamic || receipts.ContainsKey(assembly.FullName)) continue;
                string location = null, source = null, hash = null, error = null;
                try
                {
                    location = assembly.Location;
                    source = assembly.CodeBase;
                    if (!string.IsNullOrEmpty(location) && File.Exists(location))
                        using (var stream = File.OpenRead(location))
                        using (var sha = SHA256.Create())
                            hash = BitConverter.ToString(sha.ComputeHash(stream)).Replace("-", "").ToLowerInvariant();
                    else error = "Assembly has no readable file location.";
                }
                catch (Exception ex) { error = ex.Message; }
                receipts[assembly.FullName] = new { name = assembly.GetName().Name, fullName = assembly.FullName,
                    location, codeBase = source, sha256 = hash, error };
                changed = true;
            }
            return changed;
        }
    }
}
