<?php

namespace App\Console\Commands;

use App\Models\Device;
use Illuminate\Console\Command;
use Illuminate\Database\QueryException;
use Illuminate\Support\Facades\DB;
use Illuminate\Validation\ValidationException;
use JsonException;
use RuntimeException;

class ImportAxera extends Command
{
    protected $signature = 'localkit:import-axera {path}';

    protected $description = 'Import an inspected Axera D4H without publishing device commands';

    public function handle(): int
    {
        try {
            $contents = $this->argument('path') === '-' ? stream_get_contents(STDIN) : @file_get_contents($this->argument('path'));
            if ($contents === false) {
                throw new RuntimeException('Could not read the metadata file');
            }
            $metadata = json_decode($contents, true, flags: JSON_THROW_ON_ERROR);
            $profile = json_decode(file_get_contents(base_path('scripts/axera/profiles/d4h-895.json')), true, flags: JSON_THROW_ON_ERROR);
            foreach (['model', 'device_type', 'firmware', 'architecture', 'cpu', 'sha256'] as $field) {
                if (($metadata[$field] ?? null) != $profile[$field]) {
                    throw new RuntimeException('Unsupported Axera profile: '.$field);
                }
            }
            foreach (['serial_number', 'mqtt_subdomain', 'secret', 'petkit_id', 'address', 'mac'] as $field) {
                if (empty($metadata[$field])) {
                    throw new RuntimeException('Missing device metadata: '.$field);
                }
            }
            foreach (['bt_mac', 'ap_mac', 'chip_id'] as $field) {
                if (! array_key_exists($field, $metadata) || ! is_string($metadata[$field])) {
                    throw new RuntimeException('Invalid hardware metadata: '.$field);
                }
            }
            $existing = Device::whereSerialNumber($metadata['serial_number'])->first();
            if ($existing !== null) {
                if ((string) $existing->petkit_id !== (string) $metadata['petkit_id']
                    || $existing->device_type !== 'd4h' || $existing->mqtt_subdomain !== $metadata['mqtt_subdomain']) {
                    throw new RuntimeException('Existing device identity differs from the metadata');
                }
                $this->info('Device '.$existing->getKey().' is already registered');

                return self::SUCCESS;
            }
            $settings = $metadata['app_conf'] ?? [];
            if (! is_array($settings)) {
                throw new RuntimeException('Invalid device settings');
            }
            foreach (['camera_enable' => 'camera', 'mic_enable' => 'microphone', 'irlight_enable' => 'night',
                'timestamp_enable' => 'timeDisplay', 'factor1' => 'factor'] as $native => $local) {
                if (array_key_exists($native, $settings)) {
                    $settings[$local] = $settings[$native];
                }
            }
            if (isset($settings['foodWarnRange'])) {
                $settings['foodWarnRange'] = $this->range($settings['foodWarnRange']);
            }
            foreach (['lightMultiRange', 'toneMultiRange'] as $field) {
                if (isset($settings[$field])) {
                    $settings[$field] = ['name' => $field, 'ranges' => array_map($this->range(...), $settings[$field])];
                }
            }
            $device = Device::withoutEvents(fn () => DB::transaction(function () use ($metadata, $settings) {
                $device = Device::create([
                    'name' => 'YumShare Solo',
                    'serial_number' => $metadata['serial_number'],
                    'device_type' => 'd4h',
                    'firmware' => '895',
                    'petkit_id' => $metadata['petkit_id'],
                    'mqtt_subdomain' => $metadata['mqtt_subdomain'],
                    'secret' => $metadata['secret'],
                    'mac' => $metadata['mac'],
                    'bt_mac' => $metadata['bt_mac'],
                    'ap_mac' => $metadata['ap_mac'],
                    'chip_id' => $metadata['chip_id'],
                    'timezone' => $metadata['timezone'] ?? 0,
                    'locale' => $metadata['locale'] ?? 'en_US',
                    'configuration' => [
                        'settings' => $settings,
                        'states' => ['ipAddress' => $metadata['address']],
                        'capacity' => $settings['capacity'] ?? [],
                    ],
                ]);
                $device->configuration = $device->configuration()->toArray();
                $device->save();

                return $device;
            }));
            $this->info('Registered device '.$device->getKey().'; cached feeding schedules remain on the feeder');

            return self::SUCCESS;
        } catch (QueryException $exception) {
            $this->error('Device import failed at the database');

            return self::FAILURE;
        } catch (JsonException|RuntimeException|ValidationException $exception) {
            $this->error($exception->getMessage());

            return self::FAILURE;
        }
    }

    private function range(array $range): array
    {
        if (count($range) !== 2 || ! isset($range[0], $range[1])) {
            throw new RuntimeException('Invalid native time range');
        }

        return ['from' => $range[0], 'till' => $range[1]];
    }
}
