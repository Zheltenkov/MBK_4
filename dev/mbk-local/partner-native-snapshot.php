<?php
// Read-only native Laravel snapshot. No dispatch, writes, HTTP or raw client data.
require '/app/vendor/autoload.php';
$app = require '/app/bootstrap/app.php';
$app->make(Illuminate\Contracts\Console\Kernel::class)->bootstrap();
if (config('database.connections.mysql.database') !== 'partners_genuine_v1') {
    throw new RuntimeException('Unexpected native-partner schema');
}

use Illuminate\Support\Facades\DB;

$counts = [];
foreach (['leads', 'sent', 'distribution_queue', 'delivery_history', 'campaign_daily_usage', 'jobs', 'failed_jobs'] as $table) {
    $counts[$table] = DB::table($table)->count();
}
$leads = [];
foreach (DB::table('leads')->get() as $row) {
    $data = json_decode($row->data ?? '{}', true, 512, JSON_THROW_ON_ERROR);
    $leads[] = [
        'partners_local_lead_id' => $row->id,
        'genuine_bitrix_id' => $row->lead_id,
        'payload_lead_id' => $data['leadId'] ?? null,
        'program_id' => $row->partner_program_id,
        'phone_sha256_synthetic_only' => hash('sha256', (string)$row->phone),
        'debt' => $data['debtSumm'] ?? null,
        'other_debt' => $data['totalDebtOther'] ?? null,
        'region' => $data['realEstateRegion'] ?? null,
        'tag_ids' => DB::table('lead_tags')->where('lead_id', $row->id)->orderBy('tag_id')->pluck('tag_id')->all(),
    ];
}
$sent = [];
foreach (DB::table('sent')->get() as $row) {
    $response = (string)($row->response ?? '');
    preg_match('/^Статус: ([0-9]{3}) Тело: (.*)$/su', $response, $parts);
    $ack = isset($parts[2]) ? json_decode($parts[2], true) : null;
    $sent[] = [
        'sent_id' => $row->id, 'partners_local_lead_id' => $row->lead_id,
        'queue_id' => $row->queue_id, 'partner_id' => $row->partner_id,
        'program_id' => $row->partner_program_id, 'parent_id' => $row->parent_id,
        'http_status' => isset($parts[1]) ? (int)$parts[1] : null,
        'local_buyer_receipt_id' => is_array($ack) ? ($ack['receipt_id'] ?? null) : null,
        'response_sha256' => hash('sha256', $response),
    ];
}
$jobs = [];
foreach (DB::table('jobs')->get() as $row) {
    $payload = json_decode($row->payload, true, 512, JSON_THROW_ON_ERROR);
    $jobs[] = ['id' => $row->id, 'queue' => $row->queue, 'attempts' => $row->attempts,
        'reserved_at' => $row->reserved_at, 'display_name' => $payload['displayName'] ?? null,
        'payload_sha256' => hash('sha256', $row->payload)];
}
$history = DB::table('delivery_history')->select('partner_id', 'scope_tag_id', 'delivered_at')->get();
echo json_encode([
    'schema' => 'partners_genuine_v1', 'data_source' => 'user_fixture',
    'counts' => $counts, 'leads' => $leads, 'sent' => $sent, 'jobs' => $jobs,
    'queue' => DB::table('distribution_queue')->get(),
    'queue_tags' => DB::table('distribution_queue_tags')->get(),
    'daily_usage' => DB::table('campaign_daily_usage')->get(),
    'history_without_phone' => $history,
    'transactions' => DB::table('balance_transactions')->select('id', 'partner_id', 'type_id', 'queue_id', 'campaign_id', 'amount')->get(),
    'buyer_balance' => DB::table('partners')->where('id', 991001)->value('balance'),
], JSON_THROW_ON_ERROR | JSON_UNESCAPED_UNICODE) . PHP_EOL;
