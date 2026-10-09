import net from 'node:net';
import tls from 'node:tls';
import fs from 'node:fs';
import {connectBridge} from './bridge.js';

const ca = fs.readFileSync('/tls/ca.crt');
const server = net.createServer(client => connectBridge(client, () => tls.connect({
    host: 'localkit-broker', port: 443, servername: 'localkit-broker', ca, rejectUnauthorized: true,
})));
server.maxConnections = 16;
server.listen(1883, '0.0.0.0', () => console.log('Feeder MQTT bridge listening'));
for (const signal of ['SIGINT', 'SIGTERM']) process.on(signal, () => server.close(() => process.exit(0)));
