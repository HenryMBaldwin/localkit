<?php
return [
    'relay' => [
        'serial' => env('GO2RTC_RELAY_SERIAL'),
        'host' => env('GO2RTC_RELAY_HOST'),
        'public_host' => env('GO2RTC_RELAY_PUBLIC_HOST'),
    ],
    'port' => env('GO2RTC_DEVICE_PORT', 1984),
    'stream' => env('GO2RTC_DEVICE_STREAM', 'camera'),

    // Camera previews are shown as a cached still frame - ffmpeg grabs it from
    // go2rtc's /api/frame.mp4 - instead of a live stream.
    'thumbnail' => [
        'ttl' => (int) env('GO2RTC_THUMBNAIL_TTL', 10),        // cache seconds
        'timeout' => (int) env('GO2RTC_THUMBNAIL_TIMEOUT', 10), // ffmpeg timeout
        'ffmpeg' => env('FFMPEG_BINARY', 'ffmpeg'),
    ],
];
