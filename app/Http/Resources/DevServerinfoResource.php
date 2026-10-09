<?php

namespace App\Http\Resources;

use Illuminate\Http\Request;
use Illuminate\Http\Resources\Json\JsonResource;
use Illuminate\Support\Str;

class DevServerinfoResource extends PetkitHttpResource
{
    public static $wrap = 'result';

    /**
     * Transform the resource into an array.
     *
     * @return array<string, mixed>
     */
    public function toArray(Request $request): array
    {

        if ($this->resource->serial_number === config('axera.serial') && config('axera.server_info.apiServers')) {
            return config('axera.server_info');
        }

        $apiServer = 'http://api.eu-pet.com/6/';
        if($this->resource->isNextGen()) {
            $apiServer = 'https://api-eu.petkt.com/6/';
        }

        return [
            'ipServers' => [
                sprintf('http://%s/6/', config('petkit.local_ip'))
            ],
            'dns' => [],
            'apiServers' => [
                $apiServer
            ],
            'nextTick' => 3600,
            'linked' => 1
        ];
    }
}
