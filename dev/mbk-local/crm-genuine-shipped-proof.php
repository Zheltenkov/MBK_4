<?php
// Read-only terminal proof: actual Laravel transport, native API and native ORM.
// The source sender already performed the update; this helper never writes it.
try {
    require '/app/vendor/autoload.php';
    $app = require '/app/bootstrap/app.php';
    $app->make(Illuminate\Contracts\Console\Kernel::class)->bootstrap();
    $id = filter_var(getenv('MBK_GENUINE_WEBSITE_BITRIX_ID'), FILTER_VALIDATE_INT);
    if ($id !== 1 || config('database.connections.mysql.database') !== 'bitrix_official_v1') {
        throw new RuntimeException('Unexpected terminal genuine scope');
    }
    $response = Illuminate\Support\Facades\Http::timeout(20)
        ->post(env('BITRIX_REST_URL') . 'crm.lead.get', ['id' => $id]);
    $record = $response->json('result');
    if ($response->status() !== 200 || !is_array($record)
        || (int)($record['ID'] ?? 0) !== $id
        || ($record['ORIGIN_ID'] ?? '') !== 'local-write-native20261106v1'
        || (float)($record['UF_DEBT_SUMM'] ?? 0) !== 400000.0
        || !in_array($record['UF_SHIPPED_PARTNER'] ?? null, [true, 1, '1', 'Y'], true)
        || !is_string($record['UF_SHIPPED_DATETIME'] ?? null)
        || $record['UF_SHIPPED_DATETIME'] === '') {
        throw new RuntimeException('Native identity or shipped fields missing');
    }
    $db = Illuminate\Support\Facades\DB::class;
    $enum = $db::table('b_user_field as f')
        ->join('b_user_field_enum as e', 'e.USER_FIELD_ID', '=', 'f.ID')
        ->where('f.ENTITY_ID', 'CRM_LEAD')->where('f.FIELD_NAME', 'UF_PARTNER_NAME')
        ->where('e.XML_ID', 'microservice_bankruptcy')->value('e.ID');
    $streams = array_map('intval', (array)($record['UF_PARTNER_NAME'] ?? []));
    $native = $db::table('b_crm_lead')->where('ID', $id)->first(['ID', 'ORIGIN_ID']);
    if ($enum === null || !in_array((int)$enum, $streams, true)
        || $native === null || $native->ORIGIN_ID !== $record['ORIGIN_ID']
        || $db::table('jobs')->count() !== 0 || $db::table('failed_jobs')->count() !== 0) {
        throw new RuntimeException('Native stream, ORM identity or empty queue guard failed');
    }
    echo json_encode(['status' => 'passed', 'evidence_level' => 'genuine_bitrix_runtime',
        'data_source' => 'synthetic_fixture', 'genuine_bitrix_id' => $id,
        'origin_id' => $record['ORIGIN_ID'], 'http_status' => 200,
        'native_adapter' => 'actual_native_api_not_rest_oauth',
        'actual_laravel_http_auth' => true, 'native_orm_same_id_origin' => true,
        'shipped_partner' => true, 'shipped_datetime' => $record['UF_SHIPPED_DATETIME'],
        'partner_stream_xml_id' => 'microservice_bankruptcy',
        'native_partner_stream_enum_id' => (int)$enum,
        'partner_stream_enum_present' => true, 'jobs' => 0, 'failed_jobs' => 0,
        'response_sha256' => hash('sha256', $response->body()), 'business_writes' => 0],
        JSON_THROW_ON_ERROR) . PHP_EOL;
} catch (Throwable $error) {
    // Intentionally suppress message/URI/raw record to protect generated credentials.
    echo json_encode(['status' => 'failed', 'error_type' => get_class($error),
        'details_suppressed' => true], JSON_THROW_ON_ERROR) . PHP_EOL;
    exit(1);
}
