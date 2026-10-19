using System;
using System.IO;
using System.Threading;

namespace OniHarness.Runtime
{
    internal sealed class ControlFileSignals : IDisposable
    {
        private readonly FileSystemWatcher watcher;
        private int pending = 1;

        internal ControlFileSignals(string root)
        {
            watcher = new FileSystemWatcher(root)
            {
                NotifyFilter = NotifyFilters.FileName | NotifyFilters.LastWrite,
                IncludeSubdirectories = false
            };
            watcher.Created += Changed;
            watcher.Changed += Changed;
            watcher.Deleted += Changed;
            watcher.Renamed += Renamed;
            watcher.Error += LostEvents;
            watcher.EnableRaisingEvents = true;
        }

        internal bool Consume() => Interlocked.Exchange(ref pending, 0) != 0;

        private static bool IsControl(string name) => name == "render.json" || name == "run-lease.json";

        private void Changed(object sender, FileSystemEventArgs args)
        {
            if (IsControl(args.Name ?? string.Empty)) Interlocked.Exchange(ref pending, 1);
        }

        private void Renamed(object sender, RenamedEventArgs args)
        {
            if (IsControl(args.Name ?? string.Empty) || IsControl(args.OldName ?? string.Empty)) Interlocked.Exchange(ref pending, 1);
        }

        private void LostEvents(object sender, ErrorEventArgs args) => Interlocked.Exchange(ref pending, 1);

        public void Dispose() => watcher.Dispose();
    }
}
