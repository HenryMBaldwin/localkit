import copy
import hashlib
import io
import json
from pathlib import Path
import re
import socket
import shlex
import subprocess
import tarfile
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch

import axera


class AxeraTests(unittest.TestCase):
    def setUp(self):
        self.profile = json.loads((axera.HERE / 'profiles/d4h-895.json').read_text())

    def metadata(self):
        return {**self.profile, 'mqtt_host': 'fixture.example', 'address': '10.0.0.11',
                'lan_cidr': '10.0.0.0/24', 'hosts': '127.0.0.1 localhost\n10.0.0.99 fixture.example\n'}

    def test_every_firmware_file_must_match(self):
        for path in self.profile['sha256']:
            with self.subTest(path=path):
                metadata = copy.deepcopy(self.metadata())
                metadata['sha256'][path] = '0' * 64
                with self.assertRaisesRegex(RuntimeError, 'Unsupported firmware file'):
                    axera.validate_profile(metadata, self.profile)

    def test_incomplete_profile_is_rejected(self):
        metadata = self.metadata()
        metadata.pop('architecture')
        with self.assertRaisesRegex(RuntimeError, 'architecture'):
            axera.validate_profile(metadata, self.profile)

    def test_bundle_requires_same_lan(self):
        with self.assertRaisesRegex(RuntimeError, 'feeder LAN'):
            axera.make_bundle(self.metadata(), '10.1.0.1', b'')

    def test_changed_boot_hook_cannot_be_removed(self):
        connection = Mock()
        connection.run.side_effect = [(b'', 0), (b'original boot.sh', 0), (b'changed app_init.sh', 0)]
        with self.assertRaisesRegex(RuntimeError, 'Boot hook changed'):
            axera.rollback(connection)
        self.assertFalse(any('rm ' in call.args[0] for call in connection.run.call_args_list))

    def test_existing_boot_hook_blocks_installation_before_transfer(self):
        connection = Mock()
        connection.run.return_value = (b'', 0)
        with patch.object(axera, 'transfer') as transfer:
            with self.assertRaisesRegex(RuntimeError, 'existing /opt/app_init.sh'):
                axera.install(connection, b'unused')
            transfer.assert_not_called()

    def test_route_probe_cleans_up_on_failure(self):
        connection = Mock()
        connection.run.side_effect = [(b'', 0), (b'', 0), (b'route unexpectedly usable', 0), (b'', 0)]
        with self.assertRaisesRegex(RuntimeError, 'did not reject'):
            axera.probe_routes(connection)
        self.assertEqual(connection.run.call_args.args[0], 'ip route del prohibit 203.0.113.0/24')

    def test_route_probe_does_not_remove_an_existing_route(self):
        connection = Mock()
        connection.run.return_value = (b'203.0.113.0/24 dev eth0', 0)
        with self.assertRaisesRegex(RuntimeError, 'already in use'):
            axera.probe_routes(connection)
        self.assertEqual(connection.run.call_count, 1)

    def test_telnet_transfer_preserves_binary_data_and_private_permissions(self):
        paths = []
        def run(command, check=True):
            if command.startswith('sha256sum '):
                path = Path(shlex.split(command)[1])
                paths.append(path)
                return hashlib.sha256(path.read_bytes()).hexdigest().encode(), 0
            result = subprocess.run(['sh', '-c', command], capture_output=True, check=check)
            return result.stdout, result.returncode
        connection = Mock()
        connection.run.side_effect = run
        payload = bytes(range(256)) * 8
        path = Path(axera.transfer(connection, payload))
        try:
            self.assertEqual(path.read_bytes(), payload)
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
        finally:
            path.unlink()

    def test_telnet_transfer_removes_a_corrupted_upload(self):
        paths = []
        def run(command, check=True):
            if command.startswith('sha256sum '):
                paths.append(Path(shlex.split(command)[1]))
                return b'0' * 64, 0
            result = subprocess.run(['sh', '-c', command], capture_output=True, check=check)
            return result.stdout, result.returncode
        connection = Mock()
        connection.run.side_effect = run
        with self.assertRaisesRegex(RuntimeError, 'checksum does not match'):
            axera.transfer(connection, b'payload')
        self.assertFalse(paths[0].exists())

    def test_tar_path_traversal_and_symlinks_are_rejected(self):
        for name, member_type in [('../app_init.sh', tarfile.REGTYPE), ('boot.sh', tarfile.SYMTYPE)]:
            with self.subTest(name=name):
                payload = io.BytesIO()
                with tarfile.open(fileobj=payload, mode='w:gz') as archive:
                    item = tarfile.TarInfo(name)
                    item.type = member_type
                    archive.addfile(item)
                with self.assertRaises(RuntimeError):
                    axera.validate_bundle(payload.getvalue())

    def test_private_files_are_not_world_readable(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'device.json'
            axera.private_write(path, b'private data')
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            path.unlink()
            path.symlink_to(Path(directory) / 'other')
            with self.assertRaises(OSError):
                axera.private_write(path, b'private data')

    def test_fragmented_telnet_negotiation(self):
        connection = axera.Telnet.__new__(axera.Telnet)
        connection.wire = bytearray()
        connection.text = bytearray()
        connection.socket = Mock()
        connection.socket.recv.side_effect = [b'hello\xff', b'\xfb', b'\x03\xff\xffworld']
        for _ in range(3):
            connection.receive()
        self.assertEqual(connection.text, b'hello\xffworld')
        connection.socket.sendall.assert_called_once_with(b'\xff\xfd\x03')

    def test_command_output_cannot_impersonate_completion(self):
        client, server = socket.socketpair()
        connection = axera.Telnet.__new__(axera.Telnet)
        connection.socket = client
        connection.wire = bytearray()
        connection.text = bytearray()
        connection.timeout = 2
        def reply():
            command = server.recv(8192)
            marker = re.search(rb'AXERA_[a-f0-9]+', command).group()
            server.sendall(b'#!/bin/sh\r\n# comment\r\n[petkit:~]# embedded prompt\r\n' + marker + b':0\r\n[petkit:~]# ')
        thread = threading.Thread(target=reply)
        thread.start()
        try:
            output, status = connection.run('cat script')
            self.assertIn(b'# comment', output)
            self.assertIn(b'embedded prompt', output)
            self.assertEqual(status, 0)
        finally:
            client.close()
            server.close()
            thread.join()

    def test_boot_script_blocks_ipv4_and_ipv6_before_stock_startup(self):
        source = (axera.HERE / 'device/boot.sh').read_text()
        self.assertLess(source.index('prohibit 0.0.0.0/1'), source.index('/app/script/app_init.sh'))
        self.assertLess(source.index('prohibit ::/1'), source.index('/app/script/app_init.sh'))
        subprocess.run(['sh', '-n', str(axera.HERE / 'device/boot.sh')], check=True)
        subprocess.run(['sh', '-n', str(axera.HERE / 'device/camera.sh')], check=True)


if __name__ == '__main__':
    unittest.main()
