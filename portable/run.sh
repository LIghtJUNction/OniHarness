#!/usr/bin/env bash
set -euo pipefail
LAB_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
chmod u+x "$LAB_ROOT/toolchain/python/bin/python3" "$LAB_ROOT/toolchain/python/bin/python3.12" "$LAB_ROOT/toolchain/dotnet/dotnet"
export DOTNET_ROOT="$LAB_ROOT/toolchain/dotnet"
export PATH="$DOTNET_ROOT:$LAB_ROOT/toolchain/python/bin:$PATH"
export DOTNET_CLI_HOME="$LAB_ROOT/work/dotnet-home"
export NUGET_PACKAGES="$LAB_ROOT/work/nuget"
export DOTNET_CLI_TELEMETRY_OPTOUT=1
export DOTNET_SYSTEM_GLOBALIZATION_INVARIANT=1
exec "$LAB_ROOT/toolchain/python/bin/python3" "$LAB_ROOT/lab.py" "$@"
