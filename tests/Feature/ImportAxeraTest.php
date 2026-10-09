<?php

namespace Tests\Feature;

use App\Models\Device;
use Illuminate\Support\Facades\Artisan;
use Illuminate\Support\Facades\DB;
use Illuminate\Support\Facades\Event;
use PhpMqtt\Client\Facades\MQTT;
use Tests\TestCase;

class ImportAxeraTest extends TestCase
{
    protected function setUp(): void
    {
        parent::setUp();
        config(['database.default' => 'sqlite', 'database.connections.sqlite.database' => ':memory:']);
        DB::purge('sqlite');
        Artisan::call('migrate', ['--force' => true]);
        MQTT::shouldReceive('connection')->never();
        foreach (['created', 'updated'] as $event) {
            Event::listen('eloquent.'.$event.': '.Device::class, fn () => $this->fail('Import emitted a device event'));
        }
    }

    private function metadata(): array
    {
        return array_merge(json_decode(file_get_contents(base_path('scripts/axera/profiles/d4h-895.json')), true), [
            'serial_number' => 'TESTSERIAL', 'mqtt_subdomain' => 'test-product', 'secret' => 'test-secret',
            'petkit_id' => 123, 'address' => '192.168.1.20', 'timezone' => -4, 'locale' => 'en_US',
            'mac' => '001122334455', 'bt_mac' => '001122334456', 'ap_mac' => '', 'chip_id' => 'test-chip',
            'app_conf' => [
                'camera_enable' => 0, 'mic_enable' => 0, 'irlight_enable' => 1, 'timestamp_enable' => 1,
                'factor1' => 15, 'foodWarnRange' => [480, 1200],
                'lightMultiRange' => [[0, 1440]], 'toneMultiRange' => [[1320, 480]],
                'capacity' => [['name' => 'fullVideo']],
            ],
        ]);
    }

    private function import(array $metadata, int $status = 0): void
    {
        $path = tempnam(sys_get_temp_dir(), 'axera-test-');
        try {
            file_put_contents($path, json_encode($metadata, JSON_THROW_ON_ERROR));
            $this->artisan('localkit:import-axera', ['path' => $path])->assertExitCode($status);
        } finally {
            unlink($path);
        }
    }

    public function test_import_preserves_native_settings_without_device_events(): void
    {
        $this->import($this->metadata());
        $device = Device::sole();
        $this->assertSame('d4h', $device->device_type);
        $this->assertSame('test-secret', $device->secret);
        $this->assertFalse($device->configuration['settings']['camera']);
        $this->assertFalse($device->configuration['settings']['microphone']);
        $this->assertSame(15, $device->configuration['settings']['factor']);
        $this->assertSame(['from' => 480, 'till' => 1200], $device->configuration['settings']['foodWarnRange']);
        $this->assertSame([['from' => 1320, 'till' => 480]], $device->configuration['settings']['toneMultiRange']['ranges']);
        $this->assertSame([['name' => 'fullVideo']], $device->configuration['capacity']);
        $this->assertSame([], $device->configuration['schedule']);
    }

    public function test_repeat_import_preserves_existing_configuration_and_schedule(): void
    {
        $this->import($this->metadata());
        $device = Device::sole();
        $configuration = $device->configuration;
        $configuration['schedule'] = ['key' => 'default', 'checksum' => 'existing'];
        $device->configuration = $configuration;
        Device::withoutEvents(fn () => $device->save());
        $device->syncSchedule([['re' => '1111111', 'it' => [['id' => 'meal', 't' => 480, 'a' => 10]]]]);
        $groups = $device->scheduleGroups();
        $this->import($this->metadata());
        $this->assertSame(1, Device::count());
        $this->assertSame($configuration, $device->fresh()->configuration);
        $this->assertSame($groups, $device->fresh()->scheduleGroups());
    }

    public function test_changed_firmware_is_rejected(): void
    {
        $metadata = $this->metadata();
        $metadata['sha256']['/app/bin/ctrl'] = str_repeat('0', 64);
        $this->import($metadata, 1);
        $this->assertSame(0, Device::count());
    }

    public function test_invalid_native_ranges_leave_no_device(): void
    {
        $metadata = $this->metadata();
        $metadata['app_conf']['toneMultiRange'] = [[0]];
        $this->import($metadata, 1);
        $this->assertSame(0, Device::count());
    }
}
