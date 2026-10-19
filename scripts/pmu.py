"""Linux x86-64 user-space hardware counters via perf_event_open; no perf CLI needed."""
import ctypes
import os
import platform
import struct

# linux/perf_event.h: hardware event IDs, not software event IDs.
EVENTS = ("cycles", "instructions", "cacheReferences", "cacheMisses")
CONFIGS = (0, 1, 2, 3)


def _ioctl(*args):
    # Import only when using the Linux adapter, not when importing metrics/tests.
    import fcntl
    return fcntl.ioctl(*args)


class CounterGroup:
    def __init__(self, tid):
        if platform.system() != "Linux" or platform.machine() != "x86_64":
            raise RuntimeError("PMU adapter currently supports Linux x86_64 only")
        self.fds = []
        libc = ctypes.CDLL(None, use_errno=True)
        try:
            for config in CONFIGS:
                attr = bytearray(128)
                # Group read: nr, time_enabled, time_running, then values in event order.
                struct.pack_into("IIQQQQQ", attr, 0, 0, 128, config, 0, 0, 11,
                                 1 | (1 << 5) | (1 << 6))
                buffer = (ctypes.c_char * 128).from_buffer(attr)
                fd = libc.syscall(298, ctypes.byref(buffer), tid, -1,
                                  self.fds[0] if self.fds else -1, 8)
                if fd < 0:
                    code = ctypes.get_errno()
                    raise OSError(code, "perf_event_open: " + os.strerror(code))
                self.fds.append(fd)
        except BaseException:
            self.close()
            raise

    def start(self):
        raw = os.read(self.fds[0], 24 + 8 * len(EVENTS))
        if len(raw) != 24 + 8 * len(EVENTS):
            raise RuntimeError("Incomplete PMU group baseline")
        before = struct.unpack("Q" * (3 + len(EVENTS)), raw)
        self.started_times = before[1:3]
        # RESET clears counts, but Linux preserves cumulative enabled/running time.
        _ioctl(self.fds[0], 0x2403, 1)  # RESET group
        _ioctl(self.fds[0], 0x2400, 1)  # ENABLE group

    def stop(self):
        _ioctl(self.fds[0], 0x2401, 1)  # DISABLE group
        raw = os.read(self.fds[0], 24 + 8 * len(EVENTS))
        if len(raw) != 24 + 8 * len(EVENTS):
            raise RuntimeError("Incomplete PMU group read")
        size, enabled, running, *counts = struct.unpack("Q" * (3 + len(EVENTS)), raw)
        if size != len(EVENTS):
            raise RuntimeError("Unexpected PMU event count")
        enabled -= self.started_times[0]
        running -= self.started_times[1]
        result = dict(zip(EVENTS, counts))
        result.update(enabledNs=enabled, runningNs=running,
                      runningFraction=running / enabled if enabled else None)
        return result

    def close(self):
        for fd in self.fds:
            os.close(fd)
        self.fds.clear()


def metrics(counts):
    references, misses = counts["cacheReferences"], counts["cacheMisses"]
    return {"genericCacheMissRatio": misses / references if references else None,
            "cacheMissesPerThousandInstructions": 1000 * misses / counts["instructions"] if counts["instructions"] else None,
            "ipc": counts["instructions"] / counts["cycles"] if counts["cycles"] else None,
            "limits": "Kernel generic hardware cache events, architecture dependent; not a universal L1/L2/L3 hit rate. Counts exclude kernel/hypervisor work."}
