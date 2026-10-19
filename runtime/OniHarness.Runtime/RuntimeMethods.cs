using System;
using System.Linq;
using System.Reflection;

namespace OniHarness.Runtime
{
    internal static class RuntimeMethods
    {
        internal static MethodInfo RequireNonGeneric(Type type, string name, params Type[] parameters)
        {
            var matches = type.GetMethods(BindingFlags.Public | BindingFlags.NonPublic | BindingFlags.Static | BindingFlags.Instance)
                .Where(method => method.Name == name && !method.IsGenericMethod &&
                    method.GetParameters().Select(parameter => parameter.ParameterType).SequenceEqual(parameters)).ToArray();
            if (matches.Length != 1) throw new MissingMethodException(type.FullName, name);
            return matches[0];
        }
    }
}
