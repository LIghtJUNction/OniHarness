"""Linux process-group measurements. Sampling never signals or modifies processes."""
from dataclasses import dataclass
from datetime import datetime, timezone
import os
from pathlib import Path
import time


def _stat(pid):
    # comm can contain spaces and ')' characters; fields below start at state (3).
    fields = (Path('/proc') / str(pid) / 'stat').read_text().rsplit(')', 1)[1].split()
    return {'state': fields[0], 'startTicks': int(fields[19]),
            'minorFaults': int(fields[7]), 'majorFaults': int(fields[9]),
            'cpuSeconds': (int(fields[11]) + int(fields[12])) / os.sysconf('SC_CLK_TCK')}


@dataclass(frozen=True)
class ProcessIdentity:
    pid: int
    start_ticks: int

    def __post_init__(self):
        if type(self.pid) is not int or self.pid <= 0 or type(self.start_ticks) is not int or self.start_ticks < 0:
            raise ValueError('Expected positive PID and nonnegative /proc start ticks')

    @classmethod
    def capture(cls, pid):
        if type(pid) is not int or pid <= 0:
            raise ValueError('Expected positive PID')
        return cls(pid, _stat(pid)['startTicks'])


def _memory_fields(path):
    values = {}
    for line in path.read_text().splitlines():
        key, _, raw = line.partition(':')
        if raw:
            parts = raw.split()
            if parts and parts[0].isdigit():
                values[key] = int(parts[0])
    return values


def sample_process(identity):
    """Return counters only if PID start time matches before and after all reads."""
    result = {'pid': identity.pid, 'startTicks': identity.start_ticks, 'valid': False}
    try:
        before = _stat(identity.pid)
        if before['startTicks'] != identity.start_ticks:
            return {**result, 'error': 'pid_reused'}
        directory = Path('/proc') / str(identity.pid)
        memory = _memory_fields(directory / 'status')
        # Rollup is relatively cheap and accounts shared pages proportionally.
        # Permission failures remain explicit, rather than pretending PSS is RSS.
        try:
            rollup = _memory_fields(directory / 'smaps_rollup')
            pss_error = None
        except OSError as error:
            rollup = {}
            pss_error = type(error).__name__
        after = _stat(identity.pid)
        if after['startTicks'] != identity.start_ticks:
            return {**result, 'error': 'pid_reused'}
        if after['state'] == 'Z':
            return {**result, 'error': 'process_zombie'}
        if 'VmRSS' not in memory:
            return {**result, 'error': 'resident_memory_unavailable'}
        return {**result, **after, 'valid': True, 'rssKiB': memory['VmRSS'],
                'pssKiB': rollup.get('Pss'), 'swapKiB': memory.get('VmSwap', 0),
                'swapPssKiB': rollup.get('SwapPss'), 'pssError': pss_error}
    except OSError as error:
        return {**result, 'error': type(error).__name__}


@dataclass(frozen=True)
class ResourceLimits:
    # Zero disables an upper bound. MemAvailable reserves default to 1 GiB.
    max_rss_kib: int = 0
    max_pss_kib: int = 0
    max_swap_kib: int = 0
    min_available_kib: int = 1024 * 1024
    max_major_faults_per_second: float = 0

    def __post_init__(self):
        for key in ('max_rss_kib', 'max_pss_kib', 'max_swap_kib', 'min_available_kib'):
            value = getattr(self, key)
            if type(value) is not int or value < 0:
                raise ValueError(key + ' must be a nonnegative integer')
        value = self.max_major_faults_per_second
        if not isinstance(value, (int, float)) or isinstance(value, bool) or not 0 <= value <= 1e12:
            raise ValueError('max_major_faults_per_second must be finite and nonnegative')


def sample_group(processes, limits=None, previous=None):
    """Sample {instance_name: ProcessIdentity}; fail capacity checks without killing.

    previous may be an earlier returned sample. Fault/CPU deltas include only
    identical process incarnations; changed membership makes group rate unknown.
    """
    limits = limits or ResourceLimits()
    if not isinstance(processes, dict) or len(processes) > 64:
        raise ValueError('Expected at most 64 named process identities')
    if any(not isinstance(name, str) or not name or len(name) > 128 or
           not isinstance(identity, ProcessIdentity) for name, identity in processes.items()):
        raise ValueError('Invalid instance name or ProcessIdentity')
    identities = [(identity.pid, identity.start_ticks) for identity in processes.values()]
    if len(identities) != len(set(identities)):
        raise ValueError('A process may not be counted as two instances')
    now = time.monotonic()
    rows = {name: sample_process(identity) for name, identity in processes.items()}
    system = _memory_fields(Path('/proc/meminfo'))
    vmstat = dict(line.split() for line in Path('/proc/vmstat').read_text().splitlines())
    system_row = {key: system.get(key) for key in ('MemTotal', 'MemAvailable', 'SwapTotal', 'SwapFree')}
    system_row.update({'pageIns': int(vmstat['pswpin']), 'pageOuts': int(vmstat['pswpout']),
                       'majorFaults': int(vmstat['pgmajfault']), 'pageSizeBytes': os.sysconf('SC_PAGE_SIZE')})
    good = [row for row in rows.values() if row['valid']]
    totals = {key: sum(row[key] for row in good) for key in
              ('rssKiB', 'swapKiB', 'minorFaults', 'majorFaults', 'cpuSeconds')}
    totals['pssKiB'] = sum(row['pssKiB'] for row in good) if all(row['pssKiB'] is not None for row in good) else None
    totals['swapPssKiB'] = sum(row['swapPssKiB'] for row in good) if all(row['swapPssKiB'] is not None for row in good) else None
    rates = None
    if previous and now > previous.get('monotonicSeconds', now):
        prior = previous.get('processes', {})
        same = set(prior) == set(rows) and all(
            row['valid'] and prior[name].get('valid') and
            (row['pid'], row['startTicks']) == (prior[name]['pid'], prior[name]['startTicks'])
            for name, row in rows.items())
        if same:
            elapsed = now - previous['monotonicSeconds']
            rates = {key + 'PerSecond': max(0, totals[key] - previous['totals'][key]) / elapsed
                     for key in ('minorFaults', 'majorFaults', 'cpuSeconds')}
            rates.update({key + 'PerSecond': max(0, system_row[key] - previous['system'][key]) / elapsed
                          for key in ('pageIns', 'pageOuts')})
    violations = [name + ': ' + row['error'] for name, row in rows.items() if not row['valid']]
    for key, maximum in (('rssKiB', limits.max_rss_kib), ('pssKiB', limits.max_pss_kib),
                         ('swapKiB', limits.max_swap_kib)):
        if maximum and totals[key] is None:
            violations.append(key + ': measurement unavailable')
        elif maximum and totals[key] > maximum:
            violations.append(key + ': exceeds limit')
    if limits.min_available_kib:
        if system_row['MemAvailable'] is None:
            violations.append('MemAvailable: measurement unavailable')
        elif system_row['MemAvailable'] < limits.min_available_kib:
            violations.append('MemAvailable: below reserve')
    if limits.max_major_faults_per_second and rates and rates['majorFaultsPerSecond'] > limits.max_major_faults_per_second:
        violations.append('majorFaultsPerSecond: exceeds limit')
    return {'schema': 1, 'capturedAt': datetime.now(timezone.utc).isoformat(),
            'monotonicSeconds': now, 'processes': rows, 'totals': totals, 'system': system_row,
            'rates': rates, 'withinLimits': not violations, 'violations': violations,
            'limits': dict(limits.__dict__)}
