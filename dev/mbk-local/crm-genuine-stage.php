<?php
// Queue accounting only: never manufacture a lead or dispatch a substitute job.
require '/app/vendor/autoload.php';
$app = require '/app/bootstrap/app.php';
$app->make(Illuminate\Contracts\Console\Kernel::class)->bootstrap();
use Illuminate\Support\Facades\DB;

if (config('database.connections.mysql.database') !== 'bitrix_official_v1'
    || config('queue.default') !== 'database' || config('app.debug') !== true) {
    throw new RuntimeException('Unexpected genuine CRM database/queue/debug configuration');
}
$snapshot = static function (string $table): array {
    $rows = [];
    foreach (DB::table($table)->orderBy('id')->get() as $row) {
        $rows[(string)$row->id] = hash('sha256', json_encode((array)$row, JSON_THROW_ON_ERROR));
    }
    return $rows;
};
$phase = getenv('MBK_GENUINE_CRM_PHASE') ?: 'baseline';
if ($phase === 'baseline') {
    echo json_encode(['status' => 'passed', 'phase' => 'baseline',
        'jobs' => $snapshot('jobs'), 'failed_jobs' => $snapshot('failed_jobs')], JSON_THROW_ON_ERROR) . PHP_EOL;
    exit(0);
}
if (!in_array($phase, ['isolate', 'observe'], true)) {
    throw new RuntimeException('Unsupported genuine CRM stage');
}
$idText = getenv('MBK_GENUINE_WEBSITE_BITRIX_ID');
if (!is_string($idText) || !preg_match('/^[1-9][0-9]*$/D', $idText)) {
    throw new RuntimeException('A separately verified genuine website ID is required');
}
$leadId = (int)$idText;
if ($leadId >= 700000000) {
    throw new RuntimeException('Protocol-simulation ID namespace is not genuine evidence');
}
$nativeLead = DB::table('b_crm_lead')->where('ID', $leadId)->first(['ID', 'ORIGIN_ID']);
if ($nativeLead === null || $nativeLead->ORIGIN_ID !== 'local-write-native20261106v1') {
    throw new RuntimeException('Native website origin marker does not match');
}
$baseline = json_decode(getenv('MBK_GENUINE_CRM_BASELINE_JSON') ?: '', true, flags: JSON_THROW_ON_ERROR);
if (($baseline['status'] ?? '') !== 'passed' || ($baseline['phase'] ?? '') !== 'baseline') {
    throw new RuntimeException('Fresh CRM baseline is required');
}
foreach (['jobs', 'failed_jobs'] as $table) {
    foreach ($baseline[$table] as $id => $sha) {
        if (($snapshot($table)[$id] ?? '') !== $sha) {
            throw new RuntimeException('An existing genuine queue record changed');
        }
    }
}
$currentFailed = $snapshot('failed_jobs');
if (count(array_diff_key($currentFailed, $baseline['failed_jobs'])) !== 0) {
    throw new RuntimeException('A new genuine CRM failed job appeared');
}
$matches = static function (string $raw) use ($leadId): bool {
    $payload = json_decode($raw, true, flags: JSON_THROW_ON_ERROR);
    return ($payload['displayName'] ?? '') === 'App\Jobs\SendLeadMicroservice'
        && ($payload['data']['commandName'] ?? '') === 'App\Jobs\SendLeadMicroservice'
        && preg_match('/leadId";i:([0-9]+);/', $payload['data']['command'] ?? '', $parts)
        && (int)$parts[1] === $leadId
        && str_contains($payload['data']['command'] ?? '', '"microservice_bankruptcy";');
};
$newJobs = [];
foreach (DB::table('jobs')->get() as $job) {
    if (!array_key_exists((string)$job->id, $baseline['jobs'])) {
        if (!$matches($job->payload)) {
            throw new RuntimeException('Unexpected follow-up or substitute job');
        }
        $newJobs[] = $job;
    }
}
$queue = 'local_native_crm_20261106_v1';
if ($phase === 'isolate') {
    if (count($newJobs) !== 1 || DB::table('jobs')->where('queue', $queue)->exists()) {
        throw new RuntimeException('Exactly one new native website sender job is required');
    }
    $job = $newJobs[0];
    $changed = DB::table('jobs')->where('id', $job->id)->where('queue', 'default')
        ->where('attempts', 0)->whereNull('reserved_at')->where('payload', $job->payload)
        ->update(['queue' => $queue]);
    if ($changed !== 1) {
        throw new RuntimeException('Queue isolation guard failed');
    }
} elseif (count($newJobs) !== 0) {
    throw new RuntimeException('The genuine sender job was not fully consumed');
}
$resultJobs = [];
foreach (DB::table('jobs')->get() as $job) {
    $payload = json_decode($job->payload, true, flags: JSON_THROW_ON_ERROR);
    $resultJobs[] = ['id' => $job->id, 'queue' => $job->queue, 'attempts' => $job->attempts,
        'reserved_at' => $job->reserved_at, 'display_name' => $payload['displayName'] ?? null];
}
echo json_encode(['status' => 'passed', 'phase' => $phase, 'genuine_website_id' => $leadId,
    'target_queue' => $queue, 'new_sender_jobs' => count($newJobs), 'jobs' => $resultJobs,
    'failed_jobs' => $snapshot('failed_jobs')], JSON_THROW_ON_ERROR) . PHP_EOL;
