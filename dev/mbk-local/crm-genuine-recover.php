<?php
// Explicit manual post-Add recovery, NOT automatic original handoff or a new lead.
require '/app/vendor/autoload.php';
$app = require '/app/bootstrap/app.php';
$app->make(Illuminate\Contracts\Console\Kernel::class)->bootstrap();
use Illuminate\Support\Facades\DB;

if (config('database.connections.mysql.database') !== 'bitrix_official_v1'
    || config('queue.default') !== 'database' || config('app.debug') !== true) {
    throw new RuntimeException('Unexpected manual recovery runtime');
}
$ack = json_decode(getenv('MBK_GENUINE_SITE_ACK_JSON') ?: '', true, flags: JSON_THROW_ON_ERROR);
$proof = json_decode(getenv('MBK_GENUINE_AUTH_PROOF_JSON') ?: '', true, flags: JSON_THROW_ON_ERROR);
$id = filter_var(getenv('MBK_GENUINE_WEBSITE_BITRIX_ID'), FILTER_VALIDATE_INT);
if ($id === false || $id <= 0 || $id >= 700000000
    || ($ack['status'] ?? '') !== 'passed' || ($ack['phase'] ?? '') !== 'observe'
    || ($ack['run_id'] ?? '') !== 'local-write-native20261106v1'
    || (int)($ack['bitrix_lead_id'] ?? 0) !== $id || ($ack['old_queue_rows_preserved'] ?? false) !== true
    || ($ack['new_pending_jobs'] ?? -1) !== 0 || ($ack['new_failed_jobs'] ?? -1) !== 0
    || ($proof['status'] ?? '') !== 'passed' || (int)($proof['genuine_website_id'] ?? 0) !== $id
    || ($proof['actual_laravel_http_auth'] ?? false) !== true
    || ($proof['actual_bitrixrest_wrapper'] ?? false) !== true) {
    throw new RuntimeException('Verified website link/native auth proof is required');
}
if (DB::table('jobs')->count() !== 0 || DB::table('failed_jobs')->count() !== 0) {
    throw new RuntimeException('Manual recovery requires empty genuine CRM queues');
}
$response = Illuminate\Support\Facades\Http::timeout(20)
    ->post(env('BITRIX_REST_URL') . 'crm.lead.get', ['id' => $id]);
$record = $response->json('result');
if ($response->status() !== 200 || !is_array($record) || (int)($record['ID'] ?? 0) !== $id
    || ($record['ORIGIN_ID'] ?? '') !== 'local-write-native20261106v1'
    || ($record['ORIGINATOR_ID'] ?? '') !== 'local_native_harness'
    || ($record['STATUS_ID'] ?? '') !== 'RESTORED'
    || ($record['native_birthdate_iso'] ?? '') !== '1990-01-01'
    || ($record['native_address_province'] ?? '') !== 'Москва'
    || (int)($record['native_phone_work_count'] ?? 0) !== 1
    || (float)($record['UF_DEBT_SUMM'] ?? 0) !== 400000.0
    || (float)($record['OPPORTUNITY'] ?? -1) !== 0.0 || ($record['CURRENCY_ID'] ?? '') !== 'RUB'
    || !preg_match('/^[a-f0-9]{64}$/D', (string)($record['UF_LOCAL_NATIVE_PAYLOAD'] ?? ''))
    || !empty($record['UF_SHIPPED_PARTNER']) || !empty($record['UF_PARTNER_NAME'])) {
    throw new RuntimeException('Full immutable native fixture proof failed');
}
foreach (['UF_SITE_AGREE_PARTNERS' => 'local_yes', 'UF_CRM_1491296976' => 'local_bfl_moscow',
          'UF_CREDIT_HISTORY' => 'local_overdue', 'UF_CRM_1452154548' => 'local_bfl'] as $field => $xml) {
    $enum = DB::table('b_user_field as f')->join('b_user_field_enum as e', 'e.USER_FIELD_ID', '=', 'f.ID')
        ->where('f.ENTITY_ID', 'CRM_LEAD')->where('f.FIELD_NAME', $field)->where('e.XML_ID', $xml)->value('e.ID');
    if ($enum === null || (int)($record[$field] ?? 0) !== (int)$enum) {
        throw new RuntimeException('Genuine dynamic enum mapping failed');
    }
}
$native = DB::table('b_crm_lead')->where('ID', $id)->first(['ID', 'ORIGIN_ID']);
if ($native === null || $native->ORIGIN_ID !== $record['ORIGIN_ID']) {
    throw new RuntimeException('Native API/ORM identity disagree');
}
$queue = 'local_native_crm_20261106_v1';
$job = (new App\Jobs\SendLeadMicroservice($id, 'microservice_bankruptcy'))->onQueue($queue);
$app->make(Illuminate\Contracts\Bus\Dispatcher::class)->dispatch($job);
$rows = DB::table('jobs')->get();
if ($rows->count() !== 1 || $rows[0]->queue !== $queue || (int)$rows[0]->attempts !== 0
    || $rows[0]->reserved_at !== null || DB::table('failed_jobs')->count() !== 0) {
    throw new RuntimeException('Actual recovery dispatch did not produce one scoped job');
}
$payload = json_decode($rows[0]->payload, true, flags: JSON_THROW_ON_ERROR);
if (($payload['displayName'] ?? '') !== 'App\Jobs\SendLeadMicroservice'
    || ($payload['data']['commandName'] ?? '') !== 'App\Jobs\SendLeadMicroservice'
    || !preg_match('/leadId";i:([0-9]+);/', $payload['data']['command'] ?? '', $parts) || (int)$parts[1] !== $id
    || !str_contains($payload['data']['command'] ?? '', '"microservice_bankruptcy";')) {
    throw new RuntimeException('Recovery payload is not the actual native BFL sender');
}
echo json_encode(['status' => 'passed', 'phase' => 'manual_post_add_recovery',
    'automatic_original_handoff' => false, 'genuine_website_id' => $id,
    'job_id' => $rows[0]->id, 'queue' => $queue, 'attempts' => 0,
    'actual_dispatcher_jobs_created' => 1, 'native_business_writes' => 0,
    'native_get_response_sha256' => hash('sha256', $response->body()),
    'job_payload_sha256' => hash('sha256', $rows[0]->payload)], JSON_THROW_ON_ERROR) . PHP_EOL;
