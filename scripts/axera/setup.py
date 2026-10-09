#!/usr/bin/env python3
import argparse
import base64
import json
import ipaddress
import os
from pathlib import Path
import secrets
import subprocess

from axera import HERE, HOSTNAME, make_bundle, private_write, validate_profile


def main():
    parser = argparse.ArgumentParser(description='Prepare a local Axera deployment')
    parser.add_argument('--metadata', type=Path, required=True)
    parser.add_argument('--server-ip', required=True)
    parser.add_argument('--web-bind', default='127.0.0.1')
    parser.add_argument('--camera-public-host')
    parser.add_argument('--output', type=Path, default=Path('.axera/deployment'))
    parser.add_argument('--timezone', default='UTC')
    args = parser.parse_args()
    os.umask(0o077)
    metadata = json.loads(args.metadata.read_text())
    validate_profile(metadata, json.loads((HERE / 'profiles/d4h-895.json').read_text()))
    server = ipaddress.IPv4Address(args.server_ip)
    ipaddress.IPv4Address(args.web_bind)
    if server not in ipaddress.ip_network(metadata['lan_cidr']) or str(server) == metadata['address']:
        parser.error('Server address must be another address on the feeder LAN')
    if not HOSTNAME.fullmatch(metadata['mqtt_host']):
        parser.error('Invalid MQTT hostname')
    ipaddress.IPv4Address(metadata['address'])
    if args.camera_public_host and not HOSTNAME.fullmatch(args.camera_public_host):
        parser.error('Invalid public camera hostname')
    directory = args.output.resolve()
    if directory.exists():
        parser.error('Output directory already exists')
    directory.mkdir(parents=True, mode=0o700)
    tls = directory / 'tls'
    tls.mkdir(mode=0o700)
    def openssl(*arguments):
        subprocess.run(['openssl', *arguments], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    openssl('req', '-x509', '-newkey', 'rsa:2048', '-nodes', '-sha256', '-days', '3650',
            '-subj', '/CN=Localkit Local CA', '-addext', 'basicConstraints=critical,CA:TRUE',
            '-addext', 'keyUsage=critical,keyCertSign,cRLSign', '-keyout', str(tls / 'ca.key'), '-out', str(tls / 'ca.crt'))
    openssl('req', '-new', '-newkey', 'rsa:2048', '-nodes', '-subj', '/CN=localkit-broker',
            '-keyout', str(tls / 'broker.key'), '-out', str(tls / 'broker.csr'))
    extensions = 'basicConstraints=critical,CA:FALSE\nextendedKeyUsage=serverAuth\nsubjectAltName=DNS:localkit-broker,DNS:' + metadata['mqtt_host'] + ',IP:' + args.server_ip + '\n'
    private_write(tls / 'extensions', extensions.encode())
    openssl('x509', '-req', '-in', str(tls / 'broker.csr'), '-CA', str(tls / 'ca.crt'), '-CAkey', str(tls / 'ca.key'),
            '-CAcreateserial', '-CAserial', str(tls / 'ca.srl'), '-sha256', '-days', '825', '-extfile', str(tls / 'extensions'), '-out', str(tls / 'broker.crt'))
    bundle = make_bundle(metadata, args.server_ip, (tls / 'ca.crt').read_bytes())
    private_write(directory / 'device-bundle.tar.gz', bundle)
    private_write(directory / 'device.json', args.metadata.read_bytes())
    camera = {'streams': {'camera': 'http://' + metadata['address'] + '/main.ts?audio=1'},
              'api': {'listen': ':1984'}, 'rtsp': {'listen': ''}, 'webrtc': {'listen': ''}}
    private_write(directory / 'go2rtc.yaml', json.dumps(camera).encode())
    key = 'base64:' + base64.b64encode(secrets.token_bytes(32)).decode()
    repository = Path(__file__).resolve().parents[2]
    compose = {
        'name': 'localkit-axera',
        'services': {
            'localkit': {
                'build': {'context': str(repository)},
                'ports': [args.web_bind + ':8082:80'],
                'environment': {
                    'APP_ENV': 'production', 'APP_DEBUG': 'false', 'APP_KEY': key,
                    'APP_TIMEZONE': args.timezone, 'APP_URL': 'http://localhost:8082',
                    'DB_CONNECTION': 'sqlite', 'DB_DATABASE': '/var/www/html/storage/database/localkit.sqlite',
                    'LOCALKIT_HOMEASSISTANT_ENABLE': 'false', 'LOCALKIT_STORAGE_ENABLE': 'false',
                    'LOCALKIT_BROKER_HOST': 'localkit-broker', 'LOCALKIT_BROKER_PORT': '443',
                    'PETKIT_LOCAL_IP': args.server_ip, 'BYPASS_AUTH': 'false',
                    'GO2RTC_RELAY_SERIAL': metadata['serial_number'], 'GO2RTC_RELAY_HOST': 'go2rtc',
                    'GO2RTC_RELAY_PUBLIC_HOST': args.camera_public_host or (args.server_ip if args.web_bind == '0.0.0.0' else args.web_bind),
                },
                'volumes': ['database:/var/www/html/storage/database', 'storage:/var/www/html/storage/app',
                            'logs:/var/www/html/storage/logs'],
                'restart': 'unless-stopped',
            },
            'localkit-broker': {
                'image': 'ghcr.io/dwyschka/localkit-broker:main',
                'ports': [args.server_ip + ':443:443'],
                'environment': {'LOCALKIT': 'http://localkit'},
                'volumes': ['./tls/broker.crt:/app/certs/broker.crt:ro', './tls/broker.key:/app/certs/broker.key:ro'],
                'restart': 'unless-stopped',
            },
            'go2rtc': {
                'image': 'ghcr.io/alexxit/go2rtc:1.9.14@sha256:675c318b23c06fd862a61d262240c9a63436b4050d177ffc68a32710d9e05bae',
                'ports': [args.web_bind + ':1984:1984'],
                'volumes': ['./go2rtc.yaml:/config/go2rtc.yaml:ro'],
                'restart': 'unless-stopped',
            },
        },
        'volumes': {'database': {}, 'storage': {}, 'logs': {}},
    }
    private_write(directory / 'compose.json', json.dumps(compose, indent=2).encode())
    print('Deployment prepared in ' + str(directory))


if __name__ == '__main__':
    main()
