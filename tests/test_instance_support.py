"""Actual /proc and loopback TCP checks. Never starts Steam, Unity or ONI."""
import concurrent.futures
import json
import os
from pathlib import Path
import socket
import socketserver
import sys
import threading
import time
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from instance_network import FaultProfile, TCPFaultProxy
from instance_resources import ProcessIdentity, ResourceLimits, sample_group, sample_process


class Echo(socketserver.BaseRequestHandler):
    def handle(self):
        self.request.settimeout(3)
        try:
            while data := self.request.recv(65536):
                self.request.sendall(data)
        except (OSError, TimeoutError):
            pass


class EchoServer(socketserver.ThreadingTCPServer):
    allow_reuse_address = False
    daemon_threads = True


def receive_exact(client, count):
    result = b''
    while len(result) < count:
        chunk = client.recv(count - len(result))
        if not chunk:
            raise RuntimeError('Unexpected EOF')
        result += chunk
    return result


class ResourceTests(unittest.TestCase):
    def test_self_sample_is_identified_and_json_safe(self):
        identity = ProcessIdentity.capture(os.getpid())
        result = sample_process(identity)
        self.assertTrue(result['valid'])
        self.assertGreater(result['rssKiB'], 0)
        self.assertGreater(result['cpuSeconds'], 0)
        self.assertIsInstance(result['minorFaults'], int)
        self.assertGreaterEqual(result['majorFaults'], 0)
        self.assertIsNotNone(result['pssKiB'])
        json.dumps(result, allow_nan=False)
        stale = sample_process(ProcessIdentity(identity.pid, identity.start_ticks + 1))
        self.assertEqual(stale['error'], 'pid_reused')
        self.assertNotIn('rssKiB', stale)

    def test_group_limits_rates_and_duplicate_identity(self):
        identity = ProcessIdentity.capture(os.getpid())
        first = sample_group({'host': identity}, ResourceLimits(min_available_kib=0))
        self.assertTrue(first['withinLimits'])
        self.assertEqual(first['totals']['rssKiB'], first['processes']['host']['rssKiB'])
        self.assertGreater(first['system']['MemAvailable'], 0)
        second = sample_group({'host': identity}, ResourceLimits(max_rss_kib=1, min_available_kib=0), first)
        self.assertFalse(second['withinLimits'])
        self.assertIn('rssKiB: exceeds limit', second['violations'])
        self.assertIsNotNone(second['rates'])
        json.dumps(second, allow_nan=False)
        with self.assertRaises(ValueError):
            sample_group({'host': identity, 'pretend_client': identity})
        absent = sample_group({'gone': ProcessIdentity(2 ** 30, 0)}, ResourceLimits(min_available_kib=0))
        self.assertFalse(absent['withinLimits'])
        self.assertFalse(absent['processes']['gone']['valid'])
        self.assertEqual(absent['totals']['rssKiB'], 0)

    def test_limits_reject_nonfinite_boolean_and_negative(self):
        for value in (float('nan'), float('inf'), -1, True):
            with self.assertRaises(ValueError):
                ResourceLimits(max_major_faults_per_second=value)
        for value in (-1, True, 2.3):
            with self.assertRaises(ValueError):
                ResourceLimits(max_rss_kib=value)


class NetworkTests(unittest.TestCase):
    def setUp(self):
        self.server = EchoServer(('127.0.0.1', 0), Echo)
        self.server_thread = threading.Thread(target=self.server.serve_forever,
                                              kwargs={'poll_interval': 0.05}, daemon=True)
        self.server_thread.start()
        self.proxy = TCPFaultProxy(*self.server.server_address).start()

    def tearDown(self):
        self.proxy.close()
        result = self.proxy.snapshot()
        self.assertEqual(result['activeConnections'], 0)
        self.assertEqual(result['activePumpThreads'], 0)
        self.assertFalse(result['acceptThreadAlive'])
        self.server.shutdown()
        self.server.server_close()
        self.server_thread.join(timeout=2)

    def connect(self):
        client = socket.create_connection(self.proxy.listen_address, timeout=2)
        client.settimeout(3)
        return client

    def test_default_off_full_duplex_and_exact_counters(self):
        message = bytes(range(256)) * 256
        with self.connect() as client:
            client.sendall(message)
            self.assertEqual(receive_exact(client, len(message)), message)
        result = self.proxy.snapshot()
        self.assertFalse(result['profile']['enabled'])
        self.assertEqual(result['bytesUpstream'], len(message))
        self.assertEqual(result['bytesDownstream'], len(message))
        json.dumps(result, allow_nan=False)

    def test_latency_and_throughput_delay_actual_delivery(self):
        self.proxy.set_profile(FaultProfile(enabled=True, latency_ms=40, bytes_per_second=512, seed=17))
        message = b'x' * 128
        with self.connect() as client:
            started = time.monotonic()
            client.sendall(message)
            self.assertEqual(receive_exact(client, len(message)), message)
            elapsed = time.monotonic() - started
        # 128 B / 512 B/s in both directions, plus chunk delays. No fake clock.
        self.assertGreaterEqual(elapsed, 0.5)
        self.assertLess(elapsed, 3)

    def test_blackhole_stalls_and_recovery_keeps_stream_intact(self):
        with self.connect() as client:
            client.sendall(b'before')
            self.assertEqual(receive_exact(client, 6), b'before')
            self.proxy.set_profile(FaultProfile(enabled=True, blackhole=True))
            client.sendall(b'held during network outage')
            client.settimeout(0.15)
            with self.assertRaises(socket.timeout):
                client.recv(1024)
            self.proxy.set_profile(FaultProfile())
            client.settimeout(3)
            self.assertEqual(receive_exact(client, 26), b'held during network outage')
            self.assertEqual(self.proxy.snapshot()['activeConnections'], 1)

    def test_disconnect_and_new_connection_after_recovery(self):
        with self.connect() as client:
            client.sendall(b'connected')
            self.assertEqual(receive_exact(client, 9), b'connected')
            self.proxy.set_profile(FaultProfile(enabled=True, disconnect=True))
            self.assertEqual(client.recv(1), b'')
        self.assertEqual(self.proxy.snapshot()['activeConnections'], 0)
        with self.connect() as rejected:
            self.assertEqual(rejected.recv(1), b'')
        self.proxy.set_profile(FaultProfile())
        with self.connect() as restored:
            restored.sendall(b'reconnected')
            self.assertEqual(receive_exact(restored, 11), b'reconnected')
        self.assertGreaterEqual(self.proxy.snapshot()['rejected'], 1)

    def test_restoring_profile_cancels_a_long_latency_wait(self):
        self.proxy.set_profile(FaultProfile(enabled=True, latency_ms=60000))
        with self.connect() as client:
            client.sendall(b'resume')
            deadline = time.monotonic() + 2
            while self.proxy.snapshot()['readUpstream'] != 6 and time.monotonic() < deadline:
                time.sleep(0.01)
            self.assertEqual(self.proxy.snapshot()['readUpstream'], 6)
            started = time.monotonic()
            self.proxy.set_profile(FaultProfile())
            self.assertEqual(receive_exact(client, 6), b'resume')
            self.assertLess(time.monotonic() - started, 0.5)

    def test_connection_cap_rejects_excess_without_interrupting_existing(self):
        self.proxy.close()
        self.proxy = TCPFaultProxy(*self.server.server_address, max_connections=1).start()
        with self.connect() as first:
            first.sendall(b'first')
            self.assertEqual(receive_exact(first, 5), b'first')
            with self.connect() as second:
                self.assertEqual(second.recv(1), b'')
            first.sendall(b'still here')
            self.assertEqual(receive_exact(first, 10), b'still here')
            sample = self.proxy.snapshot()
            self.assertEqual(sample['activeConnections'], 1)
            self.assertEqual(sample['rejected'], 1)
            self.assertLessEqual(sample['activePumpThreads'], 2)

    def test_simultaneous_connections_and_half_close(self):
        barrier = threading.Barrier(5)
        def exchange(index):
            with self.connect() as client:
                barrier.wait(timeout=3)
                body = ('client ' + str(index)).encode()
                client.sendall(body)
                client.shutdown(socket.SHUT_WR)
                self.assertEqual(receive_exact(client, len(body)), body)
                self.assertEqual(client.recv(1), b'')
        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
            pending = [pool.submit(exchange, index) for index in range(4)]
            barrier.wait(timeout=3)
            for future in pending:
                future.result(timeout=4)
        self.assertEqual(self.proxy.snapshot()['accepted'], 4)

    def test_close_wakes_blackholed_threads_and_releases_port(self):
        self.proxy.set_profile(FaultProfile(enabled=True, blackhole=True))
        address = self.proxy.listen_address
        with self.connect() as client:
            client.sendall(b'waiting')
            deadline = time.monotonic() + 2
            while self.proxy.snapshot()['activeConnections'] != 1 and time.monotonic() < deadline:
                time.sleep(0.01)
            self.assertEqual(self.proxy.snapshot()['activeConnections'], 1)
            started = time.monotonic()
            self.proxy.close()
            self.assertLess(time.monotonic() - started, 1)
            self.assertEqual(client.recv(1), b'')
        with socket.socket() as rebound:
            rebound.bind(address)

    def test_profile_and_endpoint_validation(self):
        for host in ('0.0.0.0', '192.168.1.1', 'localhost'):
            with self.assertRaises(ValueError):
                TCPFaultProxy(host, 80)
        for arguments in ({'latency_ms': float('nan')}, {'jitter_ms': float('inf')},
                          {'bytes_per_second': -1}, {'seed': -1}, {'enabled': 1},
                          {'unexpected': True}):
            with self.assertRaises(ValueError):
                FaultProfile.from_dict(arguments)
        self.assertEqual(FaultProfile.from_dict({'seed': 42}).seed, 42)
        with self.assertRaises(ValueError):
            TCPFaultProxy('127.0.0.1', 80, max_connections=1000)


if __name__ == '__main__':
    unittest.main()
