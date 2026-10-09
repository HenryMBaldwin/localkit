<?php

return [
    'serial' => env('AXERA_SERIAL'),
    'http_secret' => env('AXERA_HTTP_SECRET'),
    'server_info' => json_decode(env('AXERA_SERVER_INFO', '{}'), true),
    'mqtt_host' => env('AXERA_MQTT_HOST'),
];
