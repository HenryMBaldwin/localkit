<?php

namespace Tests\Feature;

use App\Management\Go2RTC;
use App\Models\Device;
use Illuminate\Support\Facades\Http;
use Tests\TestCase;

class AxeraCameraRelayTest extends TestCase
{
    public function test_relay_uses_internal_requests_and_public_player_urls(): void
    {
        config(['go2rtc.relay.serial' => 'AXERA', 'go2rtc.relay.host' => 'go2rtc',
            'go2rtc.relay.public_host' => '127.0.0.1', 'go2rtc.port' => 1984]);
        $device = new Device(['serial_number' => 'AXERA', 'configuration' => ['states' => ['ipAddress' => '192.168.1.20']]]);
        $client = new Go2RTC;
        Http::fake(['http://go2rtc:1984/api/streams' => Http::response(['camera' => []])]);
        $this->assertSame(['camera'], $client->streams($device));
        $this->assertSame('http://go2rtc:1984/api/frame.mp4?src=camera', $client->frameMp4Url($device));
        $this->assertSame('http://127.0.0.1:1984/stream.html?src=camera', $client->streamUrl($device));
        Http::assertSent(fn ($request) => $request->url() === 'http://go2rtc:1984/api/streams');
    }

    public function test_other_devices_keep_their_own_camera_server(): void
    {
        config(['go2rtc.relay.serial' => 'AXERA', 'go2rtc.relay.host' => 'go2rtc',
            'go2rtc.relay.public_host' => '127.0.0.1', 'go2rtc.port' => 1984]);
        $device = new Device(['serial_number' => 'OTHER', 'configuration' => ['states' => ['ipAddress' => '192.168.1.21']]]);
        $this->assertSame('http://192.168.1.21:1984/stream.html?src=camera', (new Go2RTC)->streamUrl($device));
    }
}
