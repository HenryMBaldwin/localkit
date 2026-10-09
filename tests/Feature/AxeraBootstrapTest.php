<?php

namespace Tests\Feature;

use App\Models\Device;
use Illuminate\Support\Facades\Artisan;
use Illuminate\Support\Facades\DB;
use Illuminate\Support\Facades\Event;
use PhpMqtt\Client\Facades\MQTT;
use Tests\TestCase;

class AxeraBootstrapTest extends TestCase
{
    protected function setUp(): void
    {
        parent::setUp();
        config(['database.default' => 'sqlite', 'database.connections.sqlite.database' => ':memory:']);
        DB::purge('sqlite');
        Artisan::call('migrate', ['--force' => true]);
        $metadata = array_merge(json_decode(file_get_contents(base_path('scripts/axera/profiles/d4h-895.json')), true), [
            'serial_number' => 'TESTSERIAL', 'mqtt_subdomain' => 'test-product', 'secret' => 'test-secret',
            'petkit_id' => 123, 'address' => '192.168.1.20', 'timezone' => -4, 'locale' => 'America/New_York',
            'mac' => '001122334455', 'bt_mac' => '001122334456', 'ap_mac' => '', 'chip_id' => 'test-chip',
            'app_conf' => [],
        ]);
        $path = tempnam(sys_get_temp_dir(), 'bootstrap-test-');
        try {
            file_put_contents($path, json_encode($metadata, JSON_THROW_ON_ERROR));
            Artisan::call('localkit:import-axera', ['path' => $path]);
        } finally {
            unlink($path);
        }
        config(['axera.serial' => 'TESTSERIAL', 'axera.http_secret' => 'native-http-secret', 'axera.mqtt_host' => 'native.example', 'petkit.local_ip' => '192.168.1.10']);
        MQTT::shouldReceive('connection')->never();
    }

    public function test_boot_registration_preserves_imported_identity_and_configuration(): void
    {
        $before = Device::sole()->getAttributes();
        Event::listen('eloquent.updated: '.Device::class, fn () => $this->fail('Registration emitted a device event'));
        $this->getJson('/6/d4h/dev_signup?sn=TESTSERIAL&timezone=1&firmware=895')
            ->assertOk()->assertJsonPath('result.timezone', -4.0)->assertJsonPath('result.secret', 'native-http-secret');
        $this->assertSame($before, Device::sole()->getAttributes());
    }

    public function test_native_bootstrap_includes_petkit_and_aliyun_connection_details(): void
    {
        $this->getJson('/6/d4h/dev_only_iot_device_info_v2?sn=TESTSERIAL')->assertOk()
            ->assertJsonPath('result.petkit.iotPlatform', 'PETKIT')
            ->assertJsonPath('result.petkit.mqttHost', 'ssl://native.example:443')
            ->assertJsonPath('result.petkit.mqttIp', 'ssl://192.168.1.10:443')
            ->assertJsonPath('result.petkit.deviceSecret', 'test-secret')
            ->assertJsonPath('result.ali.iotPlatform', 'ALI');
    }

    public function test_native_client_presence_tracks_connection_and_disconnection(): void
    {
        \App\MQTT\Localkit::route('localkit/clients', ['localkit', 'd_d4h_TESTSERIAL']);
        $this->assertEquals(1, Device::sole()->mqtt_connected);
        \App\MQTT\Localkit::route('localkit/clients', ['localkit', 'd_d4h_ANOTHER']);
        $this->assertEquals(0, Device::sole()->mqtt_connected);
        \App\MQTT\Localkit::route('localkit/clients', ['localkit', 'd_d4h_TESTSERIAL|timestamp=1']);
        $this->assertEquals(1, Device::sole()->mqtt_connected);
        \App\MQTT\Localkit::route('localkit/clients', []);
        $this->assertEquals(0, Device::sole()->mqtt_connected);
    }

    public function test_server_bootstrap_preserves_native_server_settings(): void
    {
        $servers = ['apiServers' => ['https://native.example/6/', '', ''], 'ipServers' => [], 'dns' => ['8.8.8.8'], 'nextTick' => 3600, 'linked' => 1];
        config(['axera.server_info' => $servers]);
        $this->withHeader('X-Device', 'id=123&type=D4H')->getJson('/6/d4h/dev_serverinfo')->assertOk()
            ->assertExactJson(['result' => $servers]);
    }

    public function test_native_bootstrap_does_not_apply_to_another_feeder(): void
    {
        config(['axera.serial' => 'ANOTHER']);
        $this->getJson('/6/d4h/dev_only_iot_device_info_v2?sn=TESTSERIAL')->assertOk()
            ->assertJsonMissingPath('result.petkit');
    }
}
