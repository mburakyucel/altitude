"""Native local pidfd/socket fixtures plus deterministic manager replies; no service or bus access."""
from contextlib import contextmanager
import ctypes
import json
import os
from pathlib import Path
import socket
import stat
import struct
import subprocess
import sys
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest import mock

from altitude import platform
from scripts import validation_attester as attester, setup_validation_relay as setup
from tests.test_validation_relay import CONFIG

ATTEST_CONFIG = {name: CONFIG[name] for name in ('daemon_uid', 'daemon_cgroup', 'daemon_executable')}
ATTEST_CONFIG.update(relay_uid=401, system_bus_uid=102)


class Manager:
    def __init__(self, root=False):
        self.root = root
        self.pid = 501 if root else 701
        self.group = '/user.slice/user-1001.slice/user@1001.service' if root else CONFIG['daemon_cgroup']
        self.owner_uid, self.owner_pid = 0, 1
        self.active = 'active'
        self.queries = []
        self.pids = []

    def call(self, destination, path, interface, member, argument, kind):
        self.queries.append((destination, member, argument))
        if member == 'GetNameOwner':
            return ':1.4'
        if member == 'GetConnectionUnixUser':
            return self.owner_uid
        if member == 'GetConnectionUnixProcessID':
            return self.owner_pid
        if member == 'GetUnit':
            if self.pids:
                self.pid = self.pids.pop(0)
            return '/org/freedesktop/systemd1/unit/fixture'
        raise AssertionError(member)

    def property(self, destination, path, interface, member, kind):
        self.queries.append((destination, member, kind))
        return {'MainPID': self.pid, 'ControlGroup': self.group, 'ActiveState': self.active}[member]


class TestManagerAttestation(unittest.TestCase):
    def setUp(self):
        self.root, self.user = Manager(root=True), Manager()
        self.connections = []

        @contextmanager
        def connected(path, uid, **kwargs):
            self.connections.append((path, uid, kwargs))
            yield (self.root, 40) if path == platform.VALIDATION_SYSTEM_BUS else (self.user, 50)

        self.texts = {'/proc/sys/kernel/yama/ptrace_scope': '1',
                      '/proc/701/stat': '701 (daemon) ' + ' '.join(['S'] + ['0'] * 18 + ['100']),
                      '/proc/701/cgroup': '0::' + CONFIG['daemon_cgroup'] + '\n'}
        for patch in (mock.patch.object(platform, '_validation_manager_connection', side_effect=connected),
                      mock.patch.object(platform, 'validation_root_socket'),
                      mock.patch.object(platform, 'validation_pidfd_pid', side_effect=lambda fd: {30: 701, 40: 601, 50: 501}[fd]),
                      mock.patch.object(platform.os, 'getuid', return_value=1001),
                      mock.patch.object(Path, 'read_text', autospec=True, side_effect=lambda path: self.texts[str(path)]),
                      mock.patch.object(Path, 'stat', return_value=SimpleNamespace(st_dev=1, st_ino=2))):
            patch.start()
            self.addCleanup(patch.stop)

    def test_authenticated_manager_and_same_connection_before_after(self):
        self.assertTrue(platform.validation_attest_daemon(30, ATTEST_CONFIG))
        self.assertEqual(len(self.connections), 2)
        self.assertEqual(self.connections[0], (platform.VALIDATION_SYSTEM_BUS, 102, {'client': True}))
        self.assertEqual(self.connections[1], (platform.validation_user_manager_socket(1001), 1001,
                         {'client': False, 'pid': 501, 'cgroup': self.root.group + '/init.scope'}))
        root_units = [q for q in self.root.queries if q[1] == 'GetUnit']
        self.assertEqual(root_units, [(':1.4', 'GetUnit', 'user@1001.service')] * 2)
        user_units = [q for q in self.user.queries if q[1] == 'GetUnit']
        self.assertEqual(user_units, [('org.freedesktop.systemd1', 'GetUnit', 'altitude.service')] * 2)

    def test_spoofed_system_manager_name_never_authenticates(self):
        self.root.owner_uid = 1001
        self.assertFalse(platform.validation_attest_daemon(30, ATTEST_CONFIG))
        self.root.owner_uid, self.root.owner_pid = 0, 300
        self.assertFalse(platform.validation_attest_daemon(30, ATTEST_CONFIG))
        self.assertEqual(self.user.queries, [])

    def test_worker_or_child_in_daemon_cgroup_is_refused(self):
        self.user.pid = 702
        self.assertFalse(platform.validation_attest_daemon(30, ATTEST_CONFIG))

    def test_manager_daemon_restart_zero_pid_and_failed_unit_are_refused(self):
        for target, pids in ((self.root, [501, 502]), (self.user, [701, 702]), (self.user, [0])):
            self.root.pid, self.user.pid = 501, 701
            self.root.pids, self.user.pids = [], []
            target.pids = pids
            with self.subTest(pids=pids):
                self.assertFalse(platform.validation_attest_daemon(30, ATTEST_CONFIG))
        self.user.pid, self.user.active = 701, 'failed'
        self.assertFalse(platform.validation_attest_daemon(30, ATTEST_CONFIG))

    def test_executable_drift_refused_and_trusted_same_executable_reexec_allowed(self):
        with mock.patch.object(Path, 'stat', side_effect=[SimpleNamespace(st_dev=1, st_ino=9),
                                                       SimpleNamespace(st_dev=1, st_ino=2)]):
            self.assertFalse(platform.validation_attest_daemon(30, ATTEST_CONFIG))
        self.assertTrue(platform.validation_attest_daemon(30, ATTEST_CONFIG))

    def test_identity_read_refusal_and_dead_peer_fail_closed(self):
        with mock.patch.object(Path, 'stat', side_effect=PermissionError):
            self.assertFalse(platform.validation_attest_daemon(30, ATTEST_CONFIG))
        with mock.patch.object(platform, 'validation_pidfd_pid', side_effect=ValueError('exited')):
            self.assertFalse(platform.validation_attest_daemon(30, ATTEST_CONFIG))
        self.texts['/proc/sys/kernel/yama/ptrace_scope'] = '0'
        self.assertFalse(platform.validation_attest_daemon(30, ATTEST_CONFIG))


@unittest.skipUnless(sys.platform.startswith('linux'), 'Linux socket/pidfd seam')
class TestNativeAttestationTransport(unittest.TestCase):
    def test_actual_socket_peer_pidfd_and_descriptor_transfer(self):
        left, right = socket.socketpair(socket.AF_UNIX, socket.SOCK_SEQPACKET)
        with left, right, platform.validation_socket_peer(left, os.getuid()) as descriptor:
            self.assertEqual(platform.validation_pidfd_pid(descriptor), os.getpid())
            platform.validation_attestation_send(left, {'nonce': 'a' * 32}, descriptor)
            request, received = platform.validation_attestation_read(right, with_descriptor=True)
            try:
                self.assertEqual(request, {'nonce': 'a' * 32})
                self.assertEqual(platform.validation_pidfd_pid(received), os.getpid())
                self.assertFalse(os.get_inheritable(received))
            finally:
                os.close(received)

    def test_nonce_request_handler_and_no_caller_selected_identity(self):
        left, right = socket.socketpair(socket.AF_UNIX, socket.SOCK_SEQPACKET)
        with left, right, platform.validation_socket_peer(left, os.getuid()) as descriptor, \
                mock.patch.object(platform, 'validation_attester_relay', return_value=True), \
                mock.patch.object(platform, 'validation_attester_query', return_value=True) as query:
            for request, allowed in (({'nonce': 'a' * 32}, True), ({'nonce': 'b' * 32, 'unit': 'other'}, False)):
                platform.validation_attestation_send(left, request, descriptor)
                attester.handle(right, {'relay_uid': os.getuid()})
                result, _ = platform.validation_attestation_read(left)
                self.assertEqual(result, {'nonce': request['nonce'], 'allowed': allowed})
            self.assertEqual(query.call_count, 1)

    def test_missing_extra_or_truncated_descriptors_refuse(self):
        left, right = socket.socketpair(socket.AF_UNIX, socket.SOCK_SEQPACKET)
        with left, right, platform.validation_socket_peer(left, os.getuid()) as descriptor:
            for payload, fds in ((b'{}', []), (b'{}', [descriptor, descriptor]), (b'x' * 513, [descriptor])):
                control = [(socket.SOL_SOCKET, socket.SCM_RIGHTS, struct.pack(f'{len(fds)}i', *fds))] if fds else []
                left.sendmsg([payload], control)
                with self.assertRaises(ValueError):
                    platform.validation_attestation_read(right, with_descriptor=True)

    def test_dead_socket_peer_cannot_become_replacement(self):
        with tempfile.TemporaryDirectory() as directory, socket.socket(socket.AF_UNIX) as listener:
            path = str(Path(directory) / 'peer.sock')
            listener.bind(path)
            listener.listen(1)
            code = 'import socket,sys;s=socket.socket(socket.AF_UNIX);s.connect(sys.argv[1]);sys.stdin.read(1)'
            process = subprocess.Popen([sys.executable, '-I', '-c', code, path], stdin=subprocess.PIPE)
            try:
                connection, _ = listener.accept()
                with connection, self.assertRaises(ValueError):
                    with platform.validation_socket_peer(connection, os.getuid()) as descriptor:
                        self.assertEqual(platform.validation_pidfd_pid(descriptor), process.pid)
                        process.communicate(b'x', timeout=3)
            finally:
                if process.poll() is None:
                    process.kill()
                    process.wait()
                if process.stdin:
                    process.stdin.close()

    def test_native_sd_bus_binding_uses_provided_descriptor_without_endpoint_lookup(self):
        left, right = socket.socketpair()
        with left, right:
            bus = platform._ValidationBus(left, client=False)
            try:
                lib, pointer = bus.lib, ctypes.c_void_p
                lib.sd_bus_message_new_signal.argtypes = [pointer, ctypes.POINTER(pointer), ctypes.c_char_p,
                                                         ctypes.c_char_p, ctypes.c_char_p]
                lib.sd_bus_message_append_basic.argtypes = [pointer, ctypes.c_char, pointer]
                lib.sd_bus_message_seal.argtypes = [pointer, ctypes.c_uint64, ctypes.c_uint64]
                lib.sd_bus_message_rewind.argtypes = [pointer, ctypes.c_int]
                message, value = pointer(), ctypes.c_uint32(701)
                self.assertEqual(lib.sd_bus_message_new_signal(bus.bus, ctypes.byref(message),
                                 b'/fixture', b'example.fixture', b'Identity'), 0)
                try:
                    self.assertGreaterEqual(lib.sd_bus_message_append_basic(message, b'u', ctypes.byref(value)), 0)
                    self.assertGreaterEqual(lib.sd_bus_message_seal(message, 1, 0), 0)
                    self.assertGreaterEqual(lib.sd_bus_message_rewind(message, 1), 0)
                    self.assertEqual(bus._scalar(message, 'u'), 701)
                finally:
                    lib.sd_bus_message_unref(message)
            finally:
                bus.close()

    def test_native_private_bus_method_and_variant_property_round_trip(self):
        """A native private peer on a socketpair, with no host bus and no process/service launch."""
        lib = ctypes.CDLL('libsystemd.so.0')
        pointer, string, integer = ctypes.c_void_p, ctypes.c_char_p, ctypes.c_int
        class Identity(ctypes.Structure):
            _fields_ = [('bytes', ctypes.c_ubyte * 16)]
        signatures = {
            'sd_bus_new': [ctypes.POINTER(pointer)],
            'sd_bus_set_fd': [pointer, integer, integer],
            'sd_bus_set_server': [pointer, integer, Identity],
            'sd_bus_start': [pointer],
            'sd_bus_process': [pointer, ctypes.POINTER(pointer)],
            'sd_bus_wait': [pointer, ctypes.c_uint64],
            'sd_bus_message_new_method_return': [pointer, ctypes.POINTER(pointer)],
            'sd_bus_message_open_container': [pointer, ctypes.c_char, string],
            'sd_bus_message_close_container': [pointer],
            'sd_bus_message_append_basic': [pointer, ctypes.c_char, pointer],
            'sd_bus_send': [pointer, pointer, pointer],
            'sd_bus_flush': [pointer],
        }
        for name, arguments in signatures.items():
            getattr(lib, name).argtypes = arguments
            getattr(lib, name).restype = integer
        lib.sd_bus_message_get_member.argtypes = [pointer]
        lib.sd_bus_message_get_member.restype = string
        for name in ('sd_bus_close_unref', 'sd_bus_message_unref'):
            getattr(lib, name).argtypes = [pointer]
            getattr(lib, name).restype = pointer
        left, right = socket.socketpair()
        server = pointer()
        self.assertEqual(lib.sd_bus_new(ctypes.byref(server)), 0)
        descriptor = os.dup(right.fileno())
        self.assertEqual(lib.sd_bus_set_fd(server, descriptor, descriptor), 0)
        self.assertEqual(lib.sd_bus_set_server(server, 1, Identity((ctypes.c_ubyte * 16)(*range(16)))), 0)
        self.assertGreaterEqual(lib.sd_bus_start(server), 0)
        stopped, faults = threading.Event(), []
        def serve():
            try:
                while not stopped.is_set():
                    message = pointer()
                    result = lib.sd_bus_process(server, ctypes.byref(message))
                    if result < 0:
                        raise RuntimeError(f'Fixture bus failed: {result}')
                    if message:
                        reply = pointer()
                        try:
                            member = lib.sd_bus_message_get_member(message)
                            self.assertEqual(lib.sd_bus_message_new_method_return(message, ctypes.byref(reply)), 0)
                            if member == b'Get':
                                self.assertGreaterEqual(lib.sd_bus_message_open_container(reply, b'v', b'u'), 0)
                            value = ctypes.c_uint32(701)
                            self.assertGreaterEqual(lib.sd_bus_message_append_basic(reply, b'u', ctypes.byref(value)), 0)
                            if member == b'Get':
                                self.assertGreaterEqual(lib.sd_bus_message_close_container(reply), 0)
                            self.assertGreaterEqual(lib.sd_bus_send(server, reply, None), 0)
                            self.assertGreaterEqual(lib.sd_bus_flush(server), 0)
                        finally:
                            lib.sd_bus_message_unref(reply)
                            lib.sd_bus_message_unref(message)
                    elif result == 0:
                        lib.sd_bus_wait(server, 100_000)
            except BaseException as error:
                if not stopped.is_set():
                    faults.append(error)
        thread = threading.Thread(target=serve)
        thread.start()
        try:
            bus = platform._ValidationBus(left, client=False)
            try:
                self.assertEqual(bus.call('example.fixture', '/fixture', 'example.fixture', 'Echo', 'input', 'u'), 701)
                self.assertEqual(bus.property('example.fixture', '/fixture', 'example.fixture', 'MainPID', 'u'), 701)
            finally:
                stopped.set()
                bus.close()
        finally:
            stopped.set()
            thread.join(timeout=3)
            lib.sd_bus_close_unref(server)
            left.close()
            right.close()
        self.assertFalse(thread.is_alive())
        self.assertEqual(faults, [])


class TestAttesterHardening(unittest.TestCase):
    def test_system_bus_reply_sender_is_pinned_to_driver_or_verified_unique_owner(self):
        bus = platform._ValidationBus.__new__(platform._ValidationBus)
        bus.client, bus.lib = True, mock.Mock()
        for destination in ('org.freedesktop.DBus', ':1.4'):
            bus.lib.sd_bus_message_get_sender.return_value = destination.encode()
            bus._sender(None, destination)
            bus.lib.sd_bus_message_get_sender.return_value = b':1.99'
            with self.assertRaises(ValueError):
                bus._sender(None, destination)

    def test_relay_requires_fresh_verifier_and_live_original_peer(self):
        @contextmanager
        def peer(connection, uid):
            yield 40
        with mock.patch.object(platform, 'validation_socket_peer', side_effect=peer), \
                mock.patch.object(platform, 'validation_root_socket'), \
                mock.patch.object(platform.socket, 'socket'), \
                mock.patch.object(Path, 'read_text', return_value='1'), \
                mock.patch.object(platform.uuid, 'uuid4', return_value=SimpleNamespace(hex='a' * 32)), \
                mock.patch.object(platform, 'validation_attestation_send') as send, \
                mock.patch.object(platform, 'validation_attestation_read', return_value=({'nonce': 'a' * 32, 'allowed': True}, None)) as read:
            self.assertTrue(platform.validation_relay_peer(object(), CONFIG))
            self.assertEqual(send.call_args.args[1:], ({'nonce': 'a' * 32}, 40))
            read.return_value = ({'nonce': 'b' * 32, 'allowed': True}, None)
            self.assertFalse(platform.validation_relay_peer(object(), CONFIG))
            read.side_effect = TimeoutError
            self.assertFalse(platform.validation_relay_peer(object(), CONFIG))

    def test_unsupported_kernel_has_no_numeric_pid_fallback(self):
        connection = mock.Mock()
        connection.getsockopt.side_effect = [struct.pack('3i', 500, 1001, 1001), OSError('unsupported')]
        with mock.patch.object(platform.os, 'pidfd_open') as fallback, self.assertRaises(OSError):
            with platform.validation_socket_peer(connection, 1001):
                self.fail('unsupported peer admitted')
        fallback.assert_not_called()

    def test_fixed_query_process_has_whole_operation_timeout_and_sanitized_environment(self):
        with mock.patch.object(platform.subprocess, 'run', return_value=SimpleNamespace(returncode=0, stdout=b'allowed\n')) as run:
            self.assertTrue(platform.validation_attester_query(40))
        args, kwargs = run.call_args
        self.assertEqual(args[0][-2:], ['--prove', '40'])
        self.assertEqual(kwargs['env'], {'PATH': '/usr/bin:/bin', 'HOME': '/nonexistent'})
        self.assertEqual(kwargs['timeout'], 18)
        self.assertEqual(kwargs['pass_fds'], (40,))
        with mock.patch.object(platform.subprocess, 'run', side_effect=subprocess.TimeoutExpired('private', 18)):
            self.assertFalse(platform.validation_attester_query(40))

    def test_unverified_user_manager_never_reaches_native_bus(self):
        @contextmanager
        def peer(*args):
            yield 40
        with mock.patch.object(platform.socket, 'socket'), \
                mock.patch.object(platform, 'validation_socket_peer', side_effect=peer), \
                mock.patch.object(platform, 'validation_pidfd_pid', return_value=999), \
                mock.patch.object(platform, '_ValidationBus') as bus:
            with self.assertRaises(ValueError):
                with platform._validation_manager_connection('/fixture', 1001, client=False, pid=501):
                    self.fail('spoof admitted')
            bus.assert_not_called()

    def test_root_owned_activation_ancestry_is_required(self):
        leaf = Path(platform.VALIDATION_ATTESTER_SOCKET)
        def info(path):
            return SimpleNamespace(st_uid=0, st_gid=401, st_mode=(stat.S_IFSOCK | 0o660) if path == leaf else (stat.S_IFDIR | 0o755))
        with mock.patch.object(Path, 'lstat', autospec=True, side_effect=info):
            platform.validation_root_socket(str(leaf), group=401)
        def changed(path):
            value = info(path)
            if path == leaf.parent:
                value.st_uid = 1001
            return value
        with mock.patch.object(Path, 'lstat', autospec=True, side_effect=changed), self.assertRaises(ValueError):
            platform.validation_root_socket(str(leaf), group=401)

    def test_units_isolate_startup_and_do_not_transfer_runtime_directory_ownership(self):
        service = setup.attester_service(1001)
        for required in ('/usr/bin/env -i', '/usr/bin/python3 -I', 'PrivateNetwork=yes',
                         'RestrictAddressFamilies=AF_UNIX', 'CapabilityBoundingSet=\n', 'NoNewPrivileges=yes',
                         'BindReadOnlyPaths=/run/user/1001/systemd/private\n'):
            self.assertIn(required, service)
        self.assertNotIn('RuntimeDirectory=', service)
        self.assertNotIn('RuntimeDirectory=', setup.ATTESTER_SOCKET)
        self.assertIn('SocketUser=root', setup.ATTESTER_SOCKET)

    def test_python_user_startup_injection_is_ignored(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'sitecustomize.py').write_text('raise RuntimeError("injected")\n')
            user_site = root / '.local/lib' / f'python{sys.version_info.major}.{sys.version_info.minor}' / 'site-packages'
            user_site.mkdir(parents=True)
            marker = root / 'injected'
            (user_site / 'fixture.pth').write_text(f'import pathlib;pathlib.Path({str(marker)!r}).touch()\n')
            source = Path(__file__).resolve().parent.parent
            code = f'import sys;sys.path.insert(0,{str(source)!r});import scripts.validation_attester;print("isolated")'
            result = subprocess.run([sys.executable, '-I', '-c', code],
                                    env={**os.environ, 'PYTHONPATH': str(root), 'PYTHONHOME': str(root), 'HOME': str(root)},
                                    capture_output=True, text=True, timeout=10)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stdout, 'isolated\n')
            self.assertFalse(marker.exists())

    def test_untrusted_bytecode_or_package_initializer_is_rejected_before_local_imports(self):
        root = Path('/usr/local/lib/altitude-validation-relay')
        source = Path(__file__).resolve().parent.parent
        for filename in ('validation_attester.py', 'validation_relay.py'):
            prefix = (source / 'scripts' / filename).read_text().split('from altitude import platform', 1)[0]
            for relative in ('scripts/__pycache__/validation_relay.cpython-312.pyc', 'scripts/__init__.py', 'altitude/__init__.py'):
                unsafe = root / relative
                def info(path):
                    return SimpleNamespace(st_uid=1001 if path == unsafe else 0,
                                           st_mode=(stat.S_IFREG | 0o644) if path == unsafe else (stat.S_IFDIR | 0o755))
                with self.subTest(filename=filename, relative=relative), \
                        mock.patch.object(Path, 'rglob', return_value=[unsafe]), \
                        mock.patch.object(Path, 'lstat', autospec=True, side_effect=info), \
                        mock.patch.object(sys, 'flags', SimpleNamespace(isolated=1)), self.assertRaises(SystemExit):
                    exec(compile(prefix, filename, 'exec'), {'__name__': '__main__', '__file__': str(root / 'scripts' / filename)})


if __name__ == '__main__':
    unittest.main()
