<?php
// Read-only preflight through the actual Laravel Http client and BitrixRest wrapper.
// Never print URL userinfo, headers, full native records or exception messages.
try {
    require '/app/vendor/autoload.php';
    $app = require '/app/bootstrap/app.php';
    $app->make(Illuminate\Contracts\Console\Kernel::class)->bootstrap();
    $idText = getenv('MBK_GENUINE_WEBSITE_BITRIX_ID');
    if (!is_string($idText) || !preg_match('/^[1-9][0-9]*$/D', $idText) || (int)$idText >= 700000000
        || config('database.connections.mysql.database') !== 'bitrix_official_v1') {
        throw new RuntimeException('Unexpected genuine API scope');
    }
    $leadId = (int)$idText;
    $uri = env('BITRIX_REST_URL') . 'crm.lead.get';
    $response = Illuminate\Support\Facades\Http::timeout(20)->post($uri, ['id' => $leadId]);
    $value = $response->json();
    if ($response->status() !== 200 || !is_array($value) || !is_array($value['result'] ?? null)) {
        throw new RuntimeException('Native read did not return an authenticated result');
    }
    $record = $value['result'];
    if ((int)($record['ID'] ?? 0) !== $leadId || ($record['ORIGIN_ID'] ?? '') !== 'local-write-native20261106v1'
        || (float)($record['UF_DEBT_SUMM'] ?? 0) !== 400000.0) {
        throw new RuntimeException('Native API identity/origin/debt did not match');
    }
    $wrapper = App\Services\BitrixRest::callBitrixMethod('crm.lead.get', ['id' => $leadId]);
    if (!is_array($wrapper) || (int)($wrapper['result']['ID'] ?? 0) !== $leadId
        || ($wrapper['result']['ORIGIN_ID'] ?? '') !== 'local-write-native20261106v1') {
        throw new RuntimeException('Actual BitrixRest wrapper auth/result failed');
    }
    $lead = App\Models\BCrmLead::find($leadId);
    if ($lead === null || $lead->ORIGIN_ID !== 'local-write-native20261106v1'
        || (float)$lead->subData->debtSumm !== 400000.0 || ($lead->address?->PROVINCE ?? '') !== 'Москва') {
        throw new RuntimeException('Native API/ORM read connections disagree');
    }
    echo json_encode(['status' => 'passed', 'genuine_website_id' => $leadId,
        'native_adapter' => 'actual_native_api_not_rest_oauth', 'http_status' => 200,
        'actual_laravel_http_auth' => true, 'actual_bitrixrest_wrapper' => true,
        'native_orm_same_id_origin_debt_region' => true,
        'response_sha256' => hash('sha256', $response->body()), 'business_writes' => 0],
        JSON_THROW_ON_ERROR) . PHP_EOL;
} catch (Throwable $error) {
    echo json_encode(['status' => 'failed', 'error_type' => get_class($error),
        'details_suppressed' => true], JSON_THROW_ON_ERROR) . PHP_EOL;
    exit(1);
}
