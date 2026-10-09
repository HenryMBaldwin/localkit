import test from 'node:test';
import assert from 'node:assert/strict';
import net from 'node:net';
import {once} from 'node:events';
import Aedes from 'aedes';
import mqtt from 'mqtt';
import codec from 'mqtt-packet';
import {connectBridge, decodeFrames} from './bridge.js';

async function listen(server) {
    server.listen(0, '127.0.0.1');
    await once(server, 'listening');
    return server.address().port;
}

test('MQTT 5 exchanges credentials, telemetry, controls, and QoS 2 with the legacy broker', {timeout: 10000}, async () => {
    const broker = new Aedes();
    let identity;
    broker.authenticate = (client, username, password, callback) => {
        if (client.id === 'fixture-feeder') identity = [username, password.toString()];
        callback(null, true);
    };
    const brokerServer = net.createServer(broker.handle);
    const brokerPort = await listen(brokerServer);
    const bridgeServer = net.createServer(client => connectBridge(client, () => net.connect(brokerPort, '127.0.0.1')));
    const bridgePort = await listen(bridgeServer);
    let device, backend;
    try {
        backend = await mqtt.connectAsync(`mqtt://127.0.0.1:${brokerPort}`, {protocolVersion: 4, reconnectPeriod: 0});
        device = await mqtt.connectAsync(`mqtt://127.0.0.1:${bridgePort}`, {
            protocolVersion: 5, clientId: 'fixture-feeder', username: 'fixture-device', password: 'fixture-secret', reconnectPeriod: 0,
        });
        assert.deepEqual(identity, ['fixture-device', 'fixture-secret']);
        await backend.subscribeAsync('fixture/telemetry', {qos: 2});
        const telemetry = once(backend, 'message');
        await device.publishAsync('fixture/telemetry', '{"workingState":"IDLE"}', {qos: 2});
        assert.equal((await telemetry)[1].toString(), '{"workingState":"IDLE"}');
        await device.subscribeAsync('fixture/settings', {qos: 2});
        const settings = once(device, 'message');
        await backend.publishAsync('fixture/settings', '{"camera":true}', {qos: 2});
        assert.equal((await settings)[1].toString(), '{"camera":true}');
        await device.unsubscribeAsync('fixture/settings');
        assert.equal(broker.clients['fixture-feeder'].version, 4);
        await device.endAsync();
        device = null;
    } finally {
        await device?.endAsync(true);
        await backend?.endAsync(true);
        await new Promise(resolve => bridgeServer.close(resolve));
        await new Promise(resolve => broker.close(resolve));
        await new Promise(resolve => brokerServer.close(resolve));
    }
});

test('fragmented native CONNECT frames decode once', () => {
    const received = [];
    const frame = codec.generate({cmd: 'connect', protocolVersion: 5, protocolId: 'MQTT', clientId: 'fixture', clean: true, keepalive: 30}, {protocolVersion: 5});
    const decode = decodeFrames(() => 5, packet => received.push(packet), () => assert.fail('Decode failed'));
    for (const byte of frame) decode(Buffer.from([byte]));
    assert.equal(received.length, 1);
    assert.equal(received[0].protocolVersion, 5);
});

test('oversized and malformed packet lengths close the connection', () => {
    for (const bytes of [Buffer.from([0x30, 0xff, 0xff, 0x7f]), Buffer.from([0x30, 0x80, 0x80, 0x80, 0x80])]) {
        let closed = false;
        decodeFrames(() => 5, () => assert.fail('Packet accepted'), () => { closed = true; })(bytes);
        assert.equal(closed, true);
    }
});
