<?php

namespace App\Http\Resources;

use Illuminate\Http\Request;
use Illuminate\Http\Resources\Json\JsonResource;
use Illuminate\Support\Facades\Log;
use Illuminate\Support\Str;

class DevOnlyIotDeviceInfoResource extends PetkitHttpResource
{
    /**
     * Transform the resource into an array.
     *
     * @return array<string, mixed>
     */
    public function toArray(Request $request): array
    {
        $productKey = $this->mqtt_subdomain ?? Str::of(md5($this->petkit_id))->substr(0, 10);
        $region = 'eu-central-1';

        $mqtt =  sprintf('%s.iot-as-mqtt.%s.aliyuncs.com', $productKey, $region);
        if($this->ota_state) {
            $mqtt = 'noresolv.localkit.io';
        }

        $result = [
            'ali' => [
                'id' => $this->petkit_id,
                'deviceName' => sprintf('d_%s_%s', $this->device_type, $this->serial_number),
                'deviceSecret' => $this->secret ?? '',
                'iotPlatform' => 'ALI',
                'iotInstanceId' => $this->mqtt_subdomain,
                'productKey' => $productKey,
                'mqttHost' => $mqtt,
                'createdAt' => $this->created_at->timestamp * 1000,
                'type' => 1,
                'regionId' => $region
            ]
        ];
        if ($this->serial_number === config('axera.serial') && config('axera.mqtt_host')) {
            $result['petkit'] = array_merge($result['ali'], [
                'iotPlatform' => 'PETKIT',
                'mqttHost' => 'ssl://'.config('axera.mqtt_host').':443',
                'standbyMqttHost' => 'ssl://'.config('axera.mqtt_host').':443',
                'mqttIp' => 'ssl://'.config('petkit.local_ip').':443',
                'standbyMqttIp' => 'ssl://'.config('petkit.local_ip').':443',
            ]);
        }
        return $result;
    }

}
