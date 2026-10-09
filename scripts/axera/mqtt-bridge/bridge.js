import mqtt from 'mqtt-packet';

const limit = 1024 * 1024;
const acknowledgements = new Set(['puback', 'pubrec', 'pubrel', 'pubcomp']);

export function decodeFrames(version, receive, fail) {
    let pending = Buffer.alloc(0);
    return chunk => {
        try {
            pending = Buffer.concat([pending, chunk]);
            if (pending.length > limit + 5) throw new Error('Packet limit');
            while (pending.length >= 2) {
                let length = 0;
                let size = 1;
                let multiplier = 1;
                let complete = false;
                while (size < pending.length && size <= 4) {
                    const byte = pending[size++];
                    length += (byte & 127) * multiplier;
                    if (!(byte & 128)) { complete = true; break; }
                    multiplier *= 128;
                }
                if (length > limit || (!complete && size > 4)) throw new Error('Packet limit');
                if (!complete || pending.length < size + length) return;
                const parser = mqtt.parser({protocolVersion: version()});
                parser.on('error', fail);
                parser.on('packet', receive);
                parser.parse(pending.subarray(0, size + length));
                pending = pending.subarray(size + length);
            }
        } catch { fail(); }
    };
}

export function connectBridge(client, openUpstream) {
    let upstream;
    let version = 4;
    const unsubscriptions = new Map();
    const close = () => { client.destroy(); upstream?.destroy(); };
    const write = (destination, packet, protocolVersion, source) => {
        if (!destination.write(mqtt.generate(packet, {protocolVersion}))) source.pause();
    };
    client.setTimeout(120000, close);
    client.on('error', close);
    client.on('close', () => upstream?.destroy());
    client.on('drain', () => upstream?.resume());
    const fromBroker = decodeFrames(() => 4, packet => {
        try {
            if (version === 5) {
                packet.properties = {};
                if (packet.cmd === 'connack') {
                    packet.reasonCode = [0, 0x84, 0x85, 0x88, 0x86, 0x87][packet.returnCode] ?? 0x80;
                    delete packet.returnCode;
                }
                if (packet.cmd === 'unsuback') {
                    packet.granted = Array(unsubscriptions.get(packet.messageId) ?? 1).fill(0);
                    unsubscriptions.delete(packet.messageId);
                }
                if (acknowledgements.has(packet.cmd)) packet.reasonCode = 0;
            }
            write(client, packet, version, upstream);
        } catch { close(); }
    }, close);
    client.on('data', decodeFrames(() => version, packet => {
        try {
            if (!upstream) {
                if (packet.cmd !== 'connect' || ![4, 5].includes(packet.protocolVersion)) throw new Error('Connect required');
                version = packet.protocolVersion;
                if (packet.properties?.authenticationMethod || packet.properties?.sessionExpiryInterval || packet.will?.properties?.willDelayInterval) throw new Error('Unsupported session');
                packet.protocolVersion = 4;
                if (version === 5) packet.clean = true;
                upstream = openUpstream();
                const timeout = packet.keepalive ? Math.max(120000, packet.keepalive * 1500) : 0;
                client.setTimeout(timeout);
                upstream.setTimeout(timeout, close);
                upstream.on('error', close);
                upstream.on('close', () => client.destroy());
                upstream.on('drain', () => client.resume());
                upstream.on('data', fromBroker);
            } else if (packet.cmd === 'connect') throw new Error('Duplicate connect');
            if (packet.cmd === 'auth' || packet.properties?.topicAlias) throw new Error('Unsupported properties');
            if (packet.cmd === 'subscribe' && packet.subscriptions.some(item => item.nl || item.rap || item.rh)) throw new Error('Unsupported subscription');
            if (packet.cmd === 'unsubscribe') unsubscriptions.set(packet.messageId, packet.unsubscriptions.length);
            if (acknowledgements.has(packet.cmd) && packet.reasonCode >= 0x80) throw new Error('Negative acknowledgement');
            delete packet.properties;
            if (packet.will) delete packet.will.properties;
            write(upstream, packet, 4, client);
            if (packet.cmd === 'disconnect') upstream.end();
        } catch { close(); }
    }, close));
}
