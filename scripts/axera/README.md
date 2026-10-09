# Axera YumShare Solo

This adapter supports the Axera ARM D4H running firmware 895 with the hashes in `profiles/d4h-895.json`.
It installs an `/opt/app_init.sh` override, a local MQTT certificate, hostname mapping, and the native camera server.
The Docker host runs go2rtc 1.9.14, a local HTTP/TLS gateway, and an MQTT 5 to MQTT 3.1.1 bridge.
Firmware images remain unchanged. The stock feeding controller and watchdog remain active.
Registration preserves the imported HTTP identity, timezone, and native server settings.
Boot activation, local MQTT reconnection, and scheduled dispensing require an installation test.

IPv4 prohibit routes cover both halves of the address space. IPv6 receives equivalent routes when enabled.
The connected LAN route remains reachable. Native API and MQTT hostnames resolve to the local gateway. The gateway permits registration, connection bootstrap, time synchronization, and heartbeat requests. Other HTTP requests receive 403.
The feeder retains its cached feeding schedules. Those schedules are not imported into the web interface.

## Requirements

- Python 3.10 or newer, OpenSSL, and Docker Compose.
- A provisioned feeder with working root Telnet access.
- An SSH host on the feeder LAN when operating remotely.
- Reserved LAN addresses for the MQTT server and feeder.

Use the root password from the [Localkit access guide](https://localkit.io/devices/yumshare-dual.html).
The command prompts for it, or reads `DEVICE_TELNET_PASSWORD` from the environment.
Device exports, backups, deployment credentials, and private certificates belong in the ignored `.axera/` directory.

## Inspect and prepare

Run from the repository root. Replace the addresses below with your feeder and server addresses.

```sh
python3 scripts/axera/axera.py inspect --host 192.168.1.20 --via user@server --output .axera/device.json
python3 scripts/axera/axera.py probe --host 192.168.1.20 --via user@server
```

Copy `.axera/device.json` privately to the server's checkout and run these commands there:

```sh
python3 scripts/axera/setup.py --metadata .axera/device.json --server-ip 192.168.1.10
docker compose -f .axera/deployment/compose.json build
docker compose -f .axera/deployment/compose.json run --rm localkit php artisan migrate --force
docker compose -f .axera/deployment/compose.json run --rm -T localkit php artisan localkit:import-axera - < .axera/device.json
docker compose -f .axera/deployment/compose.json run --rm localkit php artisan make:filament-user
docker compose -f .axera/deployment/compose.json up -d
```

The web interface binds to server loopback port 8082. Open it through an SSH tunnel:

```sh
ssh -L 8082:127.0.0.1:8082 -L 1984:127.0.0.1:1984 user@server
```

Then visit `http://localhost:8082`. `setup.py --web-bind` can bind it to a private interface instead.
`--camera-public-host` sets the camera address used by the browser; the default follows `--web-bind`.
The device gateway binds ports 80, 443, and 8883 on the specified LAN address. This deployment uses one server LAN address and does not require macvlan.

## Install

Run on the server, or add `--via user@server` when the bundle is on another computer.

```sh
python3 scripts/axera/axera.py install --host 192.168.1.20 --bundle .axera/deployment/device-bundle.tar.gz
```

Installation saves a backup before writing the boot override and refuses unknown firmware or an existing override.
The bundle transfers through the authenticated Telnet session and is checked against its SHA-256 digest.
Reboot the feeder to activate the adapter. No reboot is issued by the installer.
Confirm the feeder reconnects to local MQTT, the camera opens at `http://localhost:1984`, and its next scheduled meal dispenses.
Camera endpoints are accessible on the feeder LAN.
This deployment directs the imported feeder's camera requests through the Docker relay.

## Rollback

```sh
python3 scripts/axera/axera.py rollback --host 192.168.1.20 --via user@server
```

Rollback removes the managed boot override. Reboot to restore stock startup and internet routing.
If firmware hashes changed, rollback still works when the boot override matches the installed adapter.
Rollback retains `/opt/localkit-axera`. Remove that directory after reboot before reinstalling.

## Tests

```sh
python3 -m unittest discover -s scripts/axera -p 'test_*.py'
php artisan test --filter 'AxeraBootstrapTest|ImportAxeraTest|AxeraCameraRelayTest'
npm --prefix scripts/axera/mqtt-bridge ci
npm --prefix scripts/axera/mqtt-bridge test
```
