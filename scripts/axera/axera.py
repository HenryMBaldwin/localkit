#!/usr/bin/env python3
import argparse
import base64
import getpass
import hashlib
import io
import ipaddress
import json
import os
from pathlib import Path
import re
import secrets
import shlex
import socket
import ssl
import subprocess
import sys
import tarfile
import time

HERE = Path(globals().get('__file__', '/tmp/axera-worker.py')).resolve().parent
BASE = '/opt/localkit-axera'
HOOK = '/opt/app_init.sh'
PROMPT = re.compile(rb'\[petkit:[^\r\n]*\]# ')
HOSTNAME = re.compile(r'(?=.{1,253}\Z)[A-Za-z0-9](?:[A-Za-z0-9.-]*[A-Za-z0-9])?\Z')


def private_write(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o600)
    with os.fdopen(descriptor, 'wb') as output:
        output.write(data)
    path.chmod(0o600)


class Telnet:
    def __init__(self, host, password, timeout=30):
        self.socket = socket.create_connection((host, 23), timeout=5)
        self.socket.settimeout(1)
        self.wire = bytearray()
        self.text = bytearray()
        self.timeout = timeout
        try:
            banner = self.until(re.compile(rb'login: '))
            if b'petkit login:' not in banner:
                raise RuntimeError('Unexpected device login banner')
            self.send('root')
            self.until(re.compile(rb'Password:'))
            self.send(password)
            self.until(PROMPT)
            self.run('stty -echo')
        except Exception:
            self.close()
            raise

    def close(self):
        self.socket.close()

    def send(self, value):
        self.socket.sendall(value.encode().replace(b'\xff', b'\xff\xff') + b'\r\n')

    def receive(self):
        data = self.socket.recv(65536)
        if not data:
            raise RuntimeError('Device closed the connection')
        self.wire.extend(data)
        while self.wire:
            if self.wire[0] != 255:
                end = self.wire.find(255)
                if end < 0:
                    end = len(self.wire)
                self.text.extend(self.wire[:end])
                del self.wire[:end]
                continue
            if len(self.wire) < 2:
                break
            command = self.wire[1]
            if command == 255:
                self.text.append(255)
                del self.wire[:2]
            elif command in (251, 252, 253, 254):
                if len(self.wire) < 3:
                    break
                option = self.wire[2]
                if command == 251:
                    self.socket.sendall(bytes([255, 253 if option in (1, 3) else 254, option]))
                elif command == 253:
                    self.socket.sendall(bytes([255, 251 if option == 3 else 252, option]))
                del self.wire[:3]
            elif command == 250:
                end = self.wire.find(b'\xff\xf0', 2)
                if end < 0:
                    break
                del self.wire[:end + 2]
            else:
                del self.wire[:2]

    def until(self, pattern):
        deadline = time.monotonic() + self.timeout
        while True:
            match = pattern.search(self.text)
            if match:
                result = bytes(self.text[:match.end()])
                del self.text[:match.end()]
                return result
            if len(self.text) > 64 * 1024 * 1024:
                raise RuntimeError('Device response exceeded 64 MiB')
            if time.monotonic() >= deadline:
                raise RuntimeError('Device command timed out')
            try:
                self.receive()
            except socket.timeout:
                continue

    def run(self, command, check=True):
        marker = 'AXERA_' + secrets.token_hex(12)
        self.send("( " + command + " ); axera_status=$?; printf '\\n" + marker + ":%s\\n' \"$axera_status\"")
        pattern = re.compile(rb'\r?\n' + marker.encode() + rb':([0-9]+)\r?\n')
        response = self.until(pattern)
        match = pattern.search(response)
        status = int(match.group(1))
        self.until(PROMPT)
        if check and status:
            raise RuntimeError('Device command failed with status ' + str(status))
        output = response[:match.start()].replace(b'\r\n', b'\n').strip(b'\n')
        return output, status

    def read(self, path):
        data, _ = self.run('base64 ' + shlex.quote(path))
        return base64.b64decode(data, validate=False)


def diagnostic_fields(output):
    fields = {}
    for line in output.decode(errors='replace').splitlines():
        line = re.sub(r'\x1b\[[0-9;]*m', '', line)
        match = re.search(r'g_config->([A-Za-z0-9_.]+)\s*=\s*(.*)', line)
        if match:
            fields[match.group(1)] = match.group(2).strip()
    return fields


def diagnostic_json(output):
    text = output.decode(errors='replace')
    decoder = json.JSONDecoder()
    for match in re.finditer(r'\{', text):
        try:
            value, _ = decoder.raw_decode(text[match.start():])
            if isinstance(value, dict):
                return value
        except json.JSONDecodeError:
            continue
    raise RuntimeError('Device configuration diagnostic did not return JSON')


def inspect(connection):
    raw, _ = connection.run('/app/bin/pktool dump')
    fields = diagnostic_fields(raw)
    device_name = fields.get('usr.iot_keys.device_name', '')
    match = re.fullmatch(r'd_(d4h)_([A-Za-z0-9]+)', device_name)
    if not match:
        raise RuntimeError('Device is not a provisioned single-hopper D4H')
    user = diagnostic_json(connection.run('/app/bin/pktool cfg cat /opt/user.conf')[0])
    architecture = connection.run('uname -m')[0].decode().strip()
    cpu = connection.run('cat /proc/cpuinfo')[0].decode()
    addresses = connection.run('ip -4 addr show wlan0')[0].decode()
    address = re.search(r'\binet\s+(\d+\.\d+\.\d+\.\d+/\d+)', addresses)
    if not address:
        raise RuntimeError('Could not identify the feeder Wi-Fi subnet')
    mqtt_host = fields.get('usr.iot_keys.mqttHost', '')
    if not HOSTNAME.fullmatch(mqtt_host):
        raise RuntimeError('Invalid device MQTT hostname')
    hashes = {}
    for path in PROFILE['sha256']:
        hashes[path] = connection.run('sha256sum ' + shlex.quote(path))[0].decode().split()[0]
    metadata = {
        'model': fields.get('dev.name'),
        'device_type': match.group(1),
        'serial_number': match.group(2),
        'device_name': device_name,
        'mac': fields.get('dev.mac_info.a_STAmac', ''),
        'bt_mac': fields.get('dev.mac_info.a_BLEmac', ''),
        'ap_mac': fields.get('dev.mac_info.a_APmac', ''),
        'chip_id': fields.get('usr.id_info.chip_id', ''),
        'firmware': fields.get('dev.version_info.ota_param.firmwareVer'),
        'architecture': architecture,
        'cpu': 'Axera_chip' if 'Axera_chip' in cpu else 'unknown',
        'address': str(ipaddress.ip_interface(address.group(1)).ip),
        'lan_cidr': str(ipaddress.ip_interface(address.group(1)).network),
        'mqtt_host': mqtt_host,
        'mqtt_subdomain': fields.get('usr.iot_keys.product_key', ''),
        'secret': fields.get('usr.iot_keys.device_secret', ''),
        'petkit_id': user.get('idInfo', {}).get('dev_id'),
        'http_secret': user.get('idInfo', {}).get('dev_srt'),
        'server_info': user.get('serverInfo', {}),
        'locale': user.get('user_info', {}).get('locale', 'en_US'),
        'timezone': float(fields.get('usr.user_info.timezone', '0')),
        'app_conf': user.get('app_conf', {}),
        'hosts': connection.read('/etc/hosts').decode(),
        'sha256': hashes,
    }
    validate_profile(metadata, PROFILE)
    return metadata


def validate_profile(metadata, profile):
    for field in ('model', 'device_type', 'firmware', 'architecture', 'cpu'):
        if metadata.get(field) != profile[field]:
            raise RuntimeError('Unsupported device ' + field)
    for path, expected in profile['sha256'].items():
        if metadata.get('sha256', {}).get(path) != expected:
            raise RuntimeError('Unsupported firmware file: ' + path)


def probe_routes(connection):
    existing, _ = connection.run('ip route show 203.0.113.0/24')
    if existing.strip():
        raise RuntimeError('Routing test prefix is already in use')
    try:
        connection.run('ip route replace prohibit 203.0.113.0/24')
        output, status = connection.run('ip route get 203.0.113.1', check=False)
        if status == 0:
            raise RuntimeError('Kernel did not reject the prohibited route')
    finally:
        connection.run('ip route del prohibit 203.0.113.0/24', check=False)


def backup(connection):
    paths = ['/opt/dev.conf', '/opt/user.conf', '/opt/cloudDomain', '/opt/version',
             '/opt/app.img', '/opt/soc.img', '/etc/hosts', '/app/script/app_init.sh',
             '/app/script/app_start.sh', '/soc/scripts/system_init.sh']
    if connection.run('test -e ' + HOOK, check=False)[1] == 0:
        paths.append(HOOK)
    if connection.run('test -d ' + BASE, check=False)[1] == 0:
        paths.append(BASE)
    command = 'tar -czf /tmp/localkit-backup.tar.gz ' + ' '.join(shlex.quote(path.lstrip('/')) for path in paths)
    connection.run('cd / && ' + command)
    try:
        payload = connection.read('/tmp/localkit-backup.tar.gz')
        with tarfile.open(fileobj=io.BytesIO(payload), mode='r:gz') as archive:
            if 'opt/user.conf' not in archive.getnames():
                raise RuntimeError('Backup archive is incomplete')
        return payload
    finally:
        connection.run('rm -f /tmp/localkit-backup.tar.gz', check=False)


def transfer(connection, payload):
    path = '/tmp/localkit-' + secrets.token_hex(24) + '.tar.gz'
    encoded = base64.b64encode(payload).decode('ascii')
    try:
        connection.run('umask 077; : > ' + path)
        for offset in range(0, len(encoded), 512):
            chunk = encoded[offset:offset + 512]
            connection.run("printf '%s' " + shlex.quote(chunk) + ' | base64 -d >> ' + path)
        digest = connection.run('sha256sum ' + path)[0].decode().split()[0]
        if digest != hashlib.sha256(payload).hexdigest():
            raise RuntimeError('Transferred bundle checksum does not match')
        return path
    except Exception:
        connection.run('rm -f ' + path, check=False)
        raise


def install(connection, payload):
    if connection.run('test -e ' + HOOK, check=False)[1] == 0:
        raise RuntimeError('An existing /opt/app_init.sh must be reviewed before installation')
    if connection.run('test -e ' + BASE, check=False)[1] == 0:
        raise RuntimeError('An existing adapter installation must be removed before installation')
    bundle_files(payload)
    probe_routes(connection)
    temporary = transfer(connection, payload)
    try:
        connection.run('mkdir -m 700 ' + BASE)
        connection.run('tar -xzf ' + temporary + ' -C ' + BASE)
        connection.run('cd ' + BASE + ' && sha256sum -c checksums')
        connection.run('chmod 700 ' + BASE + '/boot.sh ' + BASE + '/camera.sh')
        connection.run('cp ' + BASE + '/boot.sh ' + HOOK + '.new && chmod 700 ' + HOOK + '.new && mv ' + HOOK + '.new ' + HOOK)
    except Exception:
        connection.run('rm -f ' + HOOK + '.new', check=False)
        connection.run('rm -rf ' + BASE, check=False)
        raise
    finally:
        connection.run('rm -f ' + temporary, check=False)


def rollback(connection):
    connection.run('test -f ' + BASE + '/boot.sh && test -f ' + HOOK)
    expected = connection.run('sha256sum ' + BASE + '/boot.sh')[0].split()[0]
    actual = connection.run('sha256sum ' + HOOK)[0].split()[0]
    if actual != expected:
        raise RuntimeError('Boot hook changed; refusing to remove it')
    connection.run('rm ' + HOOK)


def worker(request):
    global PROFILE
    PROFILE = request['profile']
    connection = Telnet(request['host'], request['password'], timeout=60)
    try:
        action = request['action']
        if action == 'rollback':
            rollback(connection)
            return {'reboot_required': True}
        metadata = inspect(connection)
        if action == 'inspect':
            return metadata
        if action == 'probe':
            probe_routes(connection)
            return {'route_probe': 'passed'}
        if action == 'backup':
            return {'archive': base64.b64encode(backup(connection)).decode()}
        if action == 'install':
            install(connection, base64.b64decode(request['bundle']))
            return {'reboot_required': True}
        raise RuntimeError('Unknown worker action')
    finally:
        connection.close()


def call_worker(args, action, **extra):
    request = {'action': action, 'host': args.host, 'password': args.password,
               'profile': json.loads((HERE / 'profiles/d4h-895.json').read_text()), **extra}
    if not args.via:
        return worker(request)
    source = base64.b64encode(Path(__file__).read_bytes()).decode()
    program = 'import base64; exec(compile(base64.b64decode(' + repr(source) + '), "axera-worker", "exec"))'
    command = ['ssh', '-o', 'BatchMode=yes', '-o', 'ConnectTimeout=10']
    if args.ssh_host_key_alias:
        command += ['-o', 'HostKeyAlias=' + args.ssh_host_key_alias]
    command += [args.via, 'python3 -c ' + shlex.quote(program) + ' --worker']
    result = subprocess.run(command, input=json.dumps(request), capture_output=True, text=True, timeout=180)
    if result.returncode:
        raise RuntimeError(result.stderr.strip() or 'SSH worker failed')
    response = json.loads(result.stdout)
    if 'error' in response:
        raise RuntimeError(response['error'])
    return response


def bundle_files(payload):
    expected_names = {'boot.sh', 'camera.sh', 'ca.crt', 'hosts', 'checksums'}
    with tarfile.open(fileobj=io.BytesIO(payload), mode='r:gz') as archive:
        members = archive.getmembers()
        if len(members) != len(expected_names) or {item.name for item in members} != expected_names:
            raise RuntimeError('Unexpected adapter bundle contents')
        if any(not item.isfile() or item.size > 20 * 1024 * 1024 for item in members):
            raise RuntimeError('Invalid adapter bundle member')
        files = {item.name: archive.extractfile(item).read() for item in members}
    expected_checksums = ''.join(hashlib.sha256(files[name]).hexdigest() + '  ' + name + '\n' for name in files if name != 'checksums').encode()
    if files['checksums'] != expected_checksums:
        raise RuntimeError('Bundle checksums do not match')
    return files


def validate_bundle(payload):
    files = bundle_files(payload)
    for name in ('boot.sh', 'camera.sh'):
        if files[name] != (HERE / 'device' / name).read_bytes():
            raise RuntimeError('Boot scripts do not match this installer')


def local_hostnames(metadata):
    hostname = metadata['mqtt_host']
    product = metadata['mqtt_subdomain']
    if not HOSTNAME.fullmatch(hostname) or not re.fullmatch(r'[A-Za-z0-9-]+', product):
        raise RuntimeError('Invalid MQTT hostname or product key')
    return [hostname, product + '.iot-as-mqtt.eu-central-1.aliyuncs.com',
            'api.petkt.com', 'api-eu.petkt.com', 'api.eu-pet.com']


def make_bundle(metadata, server_ip, ca):
    validate_profile(metadata, json.loads((HERE / 'profiles/d4h-895.json').read_text()))
    address = ipaddress.ip_address(server_ip)
    if address not in ipaddress.ip_network(metadata['lan_cidr']):
        raise RuntimeError('MQTT server must be on the feeder LAN')
    if address == ipaddress.ip_address(metadata['address']):
        raise RuntimeError('MQTT server cannot be the feeder itself')
    hostnames = local_hostnames(metadata)
    ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT).load_verify_locations(cadata=ca.decode('ascii'))
    hosts = '\n'.join(line for line in metadata.get('hosts', '127.0.0.1 localhost\n').splitlines()
                      if not set(hostnames).intersection(part.lower() for part in line.split('#', 1)[0].split()[1:]))
    files = {
        'boot.sh': (HERE / 'device/boot.sh').read_bytes(),
        'camera.sh': (HERE / 'device/camera.sh').read_bytes(),
        'ca.crt': ca,
        'hosts': (hosts + '\n' + server_ip + ' ' + ' '.join(hostnames) + '\n').encode(),
    }
    files['checksums'] = ''.join(hashlib.sha256(value).hexdigest() + '  ' + name + '\n' for name, value in files.items()).encode()
    result = io.BytesIO()
    with tarfile.open(fileobj=result, mode='w:gz') as archive:
        for name, value in files.items():
            item = tarfile.TarInfo(name)
            item.size = len(value)
            item.mode = 0o700 if name in ('boot.sh', 'camera.sh') else 0o600
            archive.addfile(item, io.BytesIO(value))
    return result.getvalue()


def main():
    parser = argparse.ArgumentParser(description='Axera D4H firmware 895 local adapter')
    parser.add_argument('action', choices=['inspect', 'probe', 'backup', 'prepare', 'install', 'rollback'])
    parser.add_argument('--host')
    parser.add_argument('--via')
    parser.add_argument('--ssh-host-key-alias')
    parser.add_argument('--output', type=Path)
    parser.add_argument('--metadata', type=Path)
    parser.add_argument('--server-ip')
    parser.add_argument('--ca', type=Path)
    parser.add_argument('--bundle', type=Path)
    parser.add_argument('--backup-dir', type=Path, default=Path('.axera/backups'))
    args = parser.parse_args()
    if args.action == 'prepare':
        for name in ('metadata', 'server_ip', 'ca', 'output'):
            if getattr(args, name) is None:
                parser.error('--' + name.replace('_', '-') + ' is required')
        payload = make_bundle(json.loads(args.metadata.read_text()), args.server_ip, args.ca.read_bytes())
        private_write(args.output, payload)
        print('Bundle saved to ' + str(args.output))
        return
    if not args.host:
        parser.error('--host is required')
    ipaddress.IPv4Address(args.host)
    args.password = os.environ.get('DEVICE_TELNET_PASSWORD') or getpass.getpass('Feeder root password: ')
    if args.action in ('inspect', 'backup') and args.output is None:
        parser.error('--output is required')
    if args.action == 'inspect':
        metadata = call_worker(args, 'inspect')
        private_write(args.output, json.dumps(metadata, indent=2).encode())
        print('Verified ' + metadata['model'] + ' firmware ' + metadata['firmware'] + '; metadata saved to ' + str(args.output))
    elif args.action in ('backup', 'install'):
        if args.action == 'install' and args.bundle is None:
            parser.error('--bundle is required')
        if args.action == 'install':
            validate_bundle(args.bundle.read_bytes())
        result = call_worker(args, 'backup')
        path = args.output if args.action == 'backup' else args.backup_dir / (time.strftime('%Y%m%d-%H%M%S') + '.tar.gz')
        private_write(path, base64.b64decode(result['archive']))
        print('Backup saved to ' + str(path))
        if args.action == 'install':
            call_worker(args, 'install', bundle=base64.b64encode(args.bundle.read_bytes()).decode())
            print('Adapter installed. Reboot the feeder to activate it.')
    else:
        call_worker(args, args.action)
        print('Routing probe passed.' if args.action == 'probe' else 'Boot override removed. Reboot the feeder to restore stock startup.')


if __name__ == '__main__':
    if '--worker' in sys.argv:
        try:
            print(json.dumps(worker(json.load(sys.stdin))))
        except Exception as error:
            print(json.dumps({'error': str(error)}))
    else:
        try:
            main()
        except (RuntimeError, ValueError, OSError, subprocess.SubprocessError) as error:
            print(str(error), file=sys.stderr)
            sys.exit(1)
