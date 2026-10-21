"""Bounded loopback TCP impairment proxy; not UDP/P2P or internet emulation.

Delays apply per TCP read chunk in each direction, not to IP packets. A blackhole
stalls forwarding with backpressure and resumes the intact stream. Reconnection
must be performed by the client after an explicit disconnect.
"""
from dataclasses import dataclass, asdict
import ipaddress
import random
import socket
import threading
import time


@dataclass(frozen=True)
class FaultProfile:
    enabled: bool = False
    latency_ms: float = 0
    jitter_ms: float = 0
    bytes_per_second: int = 0
    blackhole: bool = False
    disconnect: bool = False
    seed: int = 0

    def __post_init__(self):
        for name in ('enabled', 'blackhole', 'disconnect'):
            if type(getattr(self, name)) is not bool:
                raise ValueError(name + ' must be boolean')
        for name in ('latency_ms', 'jitter_ms'):
            value = getattr(self, name)
            if not isinstance(value, (int, float)) or isinstance(value, bool) or not 0 <= value <= 60000:
                raise ValueError(name + ' must be finite, between 0 and 60000')
        if type(self.bytes_per_second) is not int or not 0 <= self.bytes_per_second <= 1024 ** 3:
            raise ValueError('bytes_per_second must be an integer from 0 to 1 GiB/s')
        if type(self.seed) is not int or not 0 <= self.seed < 2 ** 63:
            raise ValueError('seed must be a nonnegative signed 64 bit integer')

    @classmethod
    def from_dict(cls, value):
        if not isinstance(value, dict) or set(value) - set(cls.__dataclass_fields__):
            raise ValueError('Unknown network fault profile fields')
        return cls(**value)


def _loopback(host):
    try:
        address = ipaddress.ip_address(host)
    except ValueError as error:
        raise ValueError('Use a numeric loopback address; DNS is not accepted') from error
    if not address.is_loopback:
        raise ValueError('Proxy endpoints must be loopback addresses')
    return socket.AF_INET6 if address.version == 6 else socket.AF_INET


class TCPFaultProxy:
    def __init__(self, upstream_host, upstream_port, *, listen_host='127.0.0.1',
                 listen_port=0, profile=None, max_connections=16):
        self._upstream_family = _loopback(upstream_host)
        self._listen_family = _loopback(listen_host)
        for value, low in ((upstream_port, 1), (listen_port, 0)):
            if type(value) is not int or not low <= value <= 65535:
                raise ValueError('Invalid TCP port')
        if type(max_connections) is not int or not 1 <= max_connections <= 64:
            raise ValueError('max_connections must be between 1 and 64')
        self._upstream = (upstream_host, upstream_port)
        self._listen = (listen_host, listen_port)
        self._profile = profile or FaultProfile()
        if not isinstance(self._profile, FaultProfile):
            raise ValueError('Expected FaultProfile')
        self._maximum = max_connections
        self._lock = threading.RLock()
        self._condition = threading.Condition(self._lock)
        self._closed = threading.Event()
        self._listener = None
        self._bound_address = None
        self._accept_thread = None
        self._connections = {}
        self._threads = set()
        self._serial = 0
        self._counters = {'accepted': 0, 'rejected': 0, 'connectFailures': 0,
                          'disconnected': 0, 'bytesUpstream': 0, 'bytesDownstream': 0,
                          'readUpstream': 0, 'readDownstream': 0}

    @property
    def listen_address(self):
        if self._bound_address is None:
            raise RuntimeError('Proxy has not started')
        return self._bound_address

    def start(self):
        with self._lock:
            if self._closed.is_set():
                raise RuntimeError('Closed proxy cannot restart; create a new proxy')
            if self._listener is not None:
                return self
            listener = socket.socket(self._listen_family, socket.SOCK_STREAM)
            try:
                listener.bind(self._listen)
                listener.listen(self._maximum)
                listener.settimeout(0.2)
            except BaseException:
                listener.close()
                raise
            self._listener = listener
            self._bound_address = listener.getsockname()[:2]
            self._accept_thread = threading.Thread(target=self._accept, name='Harness TCP accept', daemon=True)
            self._accept_thread.start()
        return self

    def set_profile(self, profile):
        if not isinstance(profile, FaultProfile):
            raise ValueError('Expected FaultProfile')
        with self._condition:
            if self._closed.is_set():
                raise RuntimeError('Proxy is closed')
            self._profile = profile
            self._condition.notify_all()
            if profile.enabled and profile.disconnect:
                for serial in list(self._connections):
                    self._drop(serial)

    def _drop(self, serial):
        pair = self._connections.pop(serial, None)
        if pair is not None:
            self._counters['disconnected'] += 1
            for endpoint in pair:
                try:
                    endpoint.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass
                endpoint.close()
            self._condition.notify_all()

    def _accept(self):
        while not self._closed.is_set():
            try:
                client, _ = self._listener.accept()
            except socket.timeout:
                continue
            except OSError:
                break
            with self._condition:
                profile = self._profile
                if self._closed.is_set() or len(self._connections) >= self._maximum or \
                        len(self._threads) + 2 > self._maximum * 2 or \
                        (profile.enabled and profile.disconnect):
                    self._counters['rejected'] += 1
                    client.close()
                    continue
                server = socket.socket(self._upstream_family, socket.SOCK_STREAM)
                server.settimeout(0.5)
                try:
                    server.connect(self._upstream)
                except OSError:
                    self._counters['connectFailures'] += 1
                    client.close()
                    server.close()
                    continue
                client.settimeout(0.2)
                server.settimeout(0.2)
                self._serial += 1
                serial = self._serial
                self._connections[serial] = (client, server)
                self._counters['accepted'] += 1
                for index, (source, target, direction) in enumerate(
                        ((client, server, 'Upstream'), (server, client, 'Downstream'))):
                    worker = threading.Thread(target=self._pump,
                                              args=(serial, source, target, direction, profile.seed + serial * 2 + index),
                                              name='Harness TCP ' + str(serial) + ' ' + direction, daemon=True)
                    self._threads.add(worker)
                    worker.start()

    def _wait_forward(self, serial, deadline=0, expected_profile=None):
        with self._condition:
            while not self._closed.is_set() and serial in self._connections:
                profile = self._profile
                if profile.enabled and profile.disconnect:
                    return False
                if profile.enabled and profile.blackhole:
                    self._condition.wait(0.2)
                elif expected_profile is not None and profile is not expected_profile:
                    return True
                elif deadline > time.monotonic():
                    self._condition.wait(max(0, min(0.2, deadline - time.monotonic())))
                else:
                    return True
            return False

    def _pump(self, serial, source, target, direction, seed):
        rng = random.Random(seed)
        try:
            while self._wait_forward(serial):
                try:
                    data = source.recv(16384)
                except socket.timeout:
                    continue
                if not data:
                    # Preserve TCP half-close, so request/EOF/response protocols work.
                    try:
                        target.shutdown(socket.SHUT_WR)
                    except OSError:
                        pass
                    return
                with self._lock:
                    self._counters['read' + direction] += len(data)
                    profile = self._profile
                delay = max(0, profile.latency_ms + rng.uniform(-profile.jitter_ms, profile.jitter_ms)) / 1000 if profile.enabled else 0
                if not self._wait_forward(serial, time.monotonic() + delay, profile):
                    return
                offset = 0
                while offset < len(data) and self._wait_forward(serial):
                    with self._lock:
                        profile = self._profile
                    rate = profile.bytes_per_second if profile.enabled else 0
                    # Small bounded chunks avoid one unthrottled 16 KiB burst.
                    count = min(len(data) - offset, max(1, min(4096, rate // 20))) if rate else len(data) - offset
                    if rate and not self._wait_forward(serial, time.monotonic() + count / rate, profile):
                        return
                    try:
                        sent = target.send(data[offset:offset + count])
                    except socket.timeout:
                        continue
                    if sent <= 0:
                        return
                    offset += sent
                    with self._lock:
                        self._counters['bytes' + direction] += sent
        except OSError:
            # Endpoint disconnect is the actual outcome, not a successful delivery.
            with self._condition:
                self._drop(serial)
        finally:
            with self._condition:
                self._threads.discard(threading.current_thread())
                # Keep the other half alive until it too sees EOF.
                name = 'Harness TCP ' + str(serial) + ' '
                if not any(thread.name.startswith(name) for thread in self._threads):
                    self._drop(serial)

    def snapshot(self):
        with self._lock:
            return {'schema': 1, 'transport': 'loopback TCP stream proxy',
                    'limitations': 'Per-read-chunk latency; no UDP, Steam P2P, IP packet loss or public internet routing emulation',
                    'profile': asdict(self._profile), 'listen': list(self._bound_address or self._listen),
                    'upstream': list(self._upstream), 'maxConnections': self._maximum,
                    'activeConnections': len(self._connections), 'activePumpThreads': len(self._threads),
                    'acceptThreadAlive': bool(self._accept_thread and self._accept_thread.is_alive()),
                    'closed': self._closed.is_set(), **self._counters}

    def close(self):
        with self._condition:
            self._closed.set()
            if self._listener:
                self._listener.close()
            for serial in list(self._connections):
                self._drop(serial)
            self._condition.notify_all()
            workers = list(self._threads)
        if self._accept_thread:
            self._accept_thread.join(timeout=2)
        for worker in workers:
            worker.join(timeout=2)
        if any(worker.is_alive() for worker in workers) or (self._accept_thread and self._accept_thread.is_alive()):
            raise RuntimeError('Proxy threads failed to stop')

    def __enter__(self):
        return self.start()

    def __exit__(self, *args):
        self.close()
