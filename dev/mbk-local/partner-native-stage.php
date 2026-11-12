<?php
// Native delivery queue accounting only. Does not send data or run a worker.
require '/app/vendor/autoload.php';
$app = require '/app/bootstrap/app.php';
$app->make(Illuminate\Contracts\Console\Kernel::class)->bootstrap();
use Illuminate\Support\Facades\DB;

if (config('database.connections.mysql.database') !== 'partners_genuine_v1'
    || config('queue.default') !== 'database') {
    throw new RuntimeException('Unexpected native-partner scope');
}
$snapshot = static function (string $table): array {
    $result = [];
    foreach (DB::table($table)->orderBy('id')->get() as $row) {
        $result[(string)$row->id] = hash('sha256', json_encode((array)$row, JSON_THROW_ON_ERROR));
    }
    return $result;
};
$phase = getenv('MBK_NATIVE_PARTNER_PHASE') ?: 'baseline';
if ($phase === 'baseline') {
    foreach (['leads', 'sent', 'distribution_queue', 'delivery_history', 'campaign_daily_usage', 'jobs', 'failed_jobs'] as $table) {
        if (DB::table($table)->count() !== 0) {
            throw new RuntimeException('Pre-input native partner evidence must be empty');
        }
    }
    echo json_encode(['status' => 'passed', 'phase' => 'baseline',
        'jobs' => $snapshot('jobs'), 'failed_jobs' => $snapshot('failed_jobs')], JSON_THROW_ON_ERROR) . PHP_EOL;
    exit(0);
}
if (!in_array($phase, ['isolate', 'observe'], true)) {
    throw new RuntimeException('Unsupported native-partner phase');
}
$idText = getenv('MBK_GENUINE_WEBSITE_BITRIX_ID');
if (!is_string($idText) || !preg_match('/^[1-9][0-9]*$/D', $idText) || (int)$idText >= 700000000) {
    throw new RuntimeException('Verified genuine website ID is required');
}
$leadId = (int)$idText;
$baseline = json_decode(getenv('MBK_NATIVE_PARTNER_BASELINE_JSON') ?: '', true, flags: JSON_THROW_ON_ERROR);
if (($baseline['status'] ?? '') !== 'passed' || ($baseline['phase'] ?? '') !== 'baseline') {
    throw new RuntimeException('Fresh native partner baseline is required');
}
foreach (['jobs', 'failed_jobs'] as $table) {
    $current = $snapshot($table);
    foreach ($baseline[$table] as $id => $sha) {
        if (($current[$id] ?? '') !== $sha) {
            throw new RuntimeException('A pre-existing native-partner queue row changed');
        }
    }
    if ($table === 'failed_jobs' && count(array_diff_key($current, $baseline[$table])) !== 0) {
        throw new RuntimeException('A new native-partner failed job appeared');
    }
}
$leads = DB::table('leads')->get();
if ($leads->count() !== 1 || (int)$leads[0]->lead_id !== $leadId || (int)$leads[0]->partner_program_id !== 3) {
    throw new RuntimeException('Expected one native partner lead bound to the website ID');
}
$localLead = $leads[0];
$data = json_decode($localLead->data, true, flags: JSON_THROW_ON_ERROR);
// Match the native tagger's integer contribution; never write null as invented zero.
if ((int)($data['leadId'] ?? 0) !== $leadId || (int)($data['debtSumm'] ?? 0) !== 400000
    || (int)($data['totalDebtOther'] ?? 0) !== 0 || ($data['realEstateRegion'] ?? '') !== 'Москва') {
    throw new RuntimeException('Native sender payload does not match the reviewed fixture');
}
$items = DB::table('distribution_queue')->get();
if ($items->count() !== 1 || (int)$items[0]->lead_id !== (int)$localLead->id
    || (int)$items[0]->campaign_id !== 950 || (int)$items[0]->partner_id !== 991001
    || (int)$items[0]->scope_tag_id !== 950 || (int)$items[0]->delivery_order !== 1
    || (float)$items[0]->price !== 10.0) {
    throw new RuntimeException('Native campaign/queue identity does not match');
}
$item = $items[0];
$matches = static function (string $raw) use ($item): bool {
    $payload = json_decode($raw, true, flags: JSON_THROW_ON_ERROR);
    return ($payload['displayName'] ?? '') === 'App\Jobs\LeadSend'
        && ($payload['data']['commandName'] ?? '') === 'App\Jobs\LeadSend'
        && preg_match('/queueId";i:([0-9]+);/', $payload['data']['command'] ?? '', $parts)
        && (int)$parts[1] === (int)$item->id;
};
$newJobs = [];
foreach (DB::table('jobs')->get() as $job) {
    if (!array_key_exists((string)$job->id, $baseline['jobs'])) {
        if (!$matches($job->payload)) {
            throw new RuntimeException('Unexpected native delivery follow-up job');
        }
        $newJobs[] = $job;
    }
}
$queue = 'local_native_buyer_20261106_v1';
if ($phase === 'isolate') {
    if ((int)$item->status_id !== 1 || count($newJobs) !== 1 || DB::table('sent')->count() !== 0
        || DB::table('delivery_history')->count() !== 0 || DB::table('jobs')->where('queue', $queue)->exists()) {
        throw new RuntimeException('Exactly one fresh unsent native delivery is required');
    }
    $job = $newJobs[0];
    if (DB::table('jobs')->where('id', $job->id)->where('queue', 'default')->where('attempts', 0)
        ->whereNull('reserved_at')->where('payload', $job->payload)->update(['queue' => $queue]) !== 1) {
        throw new RuntimeException('Native delivery isolation guard failed');
    }
} else {
    $sent = DB::table('sent')->where('lead_id', $localLead->id)->where('queue_id', $item->id)->get();
    if ((int)$item->status_id !== 2 || count($newJobs) !== 0 || $sent->count() !== 1
        || !str_starts_with($sent[0]->response ?? '', 'Статус: 200 Тело: ')
        || DB::table('delivery_history')->count() !== 1
        || DB::table('balance_transactions')->where('type_id', 4)->where('queue_id', $item->id)
            ->where('campaign_id', 950)->where('partner_id', 991001)->where('amount', 10)->count() !== 1
        || (float)DB::table('partners')->where('id', 991001)->value('balance') !== 90.0) {
        throw new RuntimeException('Native receipt/history/charge/accounting did not complete');
    }
}
echo json_encode(['status' => 'passed', 'phase' => $phase, 'genuine_website_id' => $leadId,
    'partners_local_lead_id' => $localLead->id, 'queue_id' => $item->id, 'campaign_id' => 950,
    'target_queue' => $queue, 'new_delivery_jobs' => count($newJobs)], JSON_THROW_ON_ERROR) . PHP_EOL;
