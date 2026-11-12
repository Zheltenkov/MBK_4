<?php
// Only the newly marked site's job may move; existing work is retained byte-for-byte.
try {
    require '/app/vendor/autoload.php';
    $app = require '/app/bootstrap/app.php';
    $app->instance('request', Illuminate\Http\Request::create('http://site-genuine:8000/local-stage'));
    $app->make(Illuminate\Contracts\Http\Kernel::class)->bootstrap();
    if (getenv('BITRIX24_URL') !== 'http://crm-genuine:8000' || getenv('DB_DATABASE') !== 'site') {
        throw new RuntimeException('Unexpected genuine-site runtime scope');
    }
    $db = Illuminate\Support\Facades\DB::class;
    $snapshot = static function (string $table) use ($db): array {
        $rows = [];
        foreach ($db::table($table)->orderBy('id')->get() as $row) {
            // Store hashes instead of payloads; retries and reservation changes remain detectable.
            $rows[(string)$row->id] = hash('sha256', json_encode((array)$row, JSON_THROW_ON_ERROR));
        }
        return $rows;
    };
    $phase = getenv('MBK_NATIVE_SITE_PHASE') ?: 'observe';
    if ($phase === 'baseline') {
        echo json_encode(['status' => 'passed', 'phase' => $phase,
            'jobs' => $snapshot('jobs'), 'failed_jobs' => $snapshot('failed_jobs')], JSON_THROW_ON_ERROR) . PHP_EOL;
        exit(0);
    }
    if (!in_array($phase, ['isolate', 'retry', 'observe'], true)) {
        throw new RuntimeException('Unsupported genuine-site phase');
    }
    $run = getenv('MBK_LOCAL_WRITE_RUN_ID');
    if (!is_string($run) || !preg_match('/^local-write-[A-Za-z0-9]+$/D', $run)) {
        throw new RuntimeException('Invalid native fixture run ID');
    }
    $baseline = json_decode(getenv('MBK_NATIVE_SITE_BASELINE_JSON') ?: '', true, flags: JSON_THROW_ON_ERROR);
    if (($baseline['status'] ?? '') !== 'passed' || ($baseline['phase'] ?? '') !== 'baseline') {
        throw new RuntimeException('A fresh baseline is required');
    }
    foreach (['jobs', 'failed_jobs'] as $table) {
        $current = $snapshot($table);
        foreach ($baseline[$table] as $id => $sha) {
            if (($current[$id] ?? '') !== $sha) {
                throw new RuntimeException('An existing queue row changed or disappeared');
            }
        }
    }
    $rows = $db::table('leads_bfl')->where('utm_source', 'local_write_stage')->where('utm_campaign', $run)
        ->selectRaw('leads_bfl.*, CAST(is_filled_out AS UNSIGNED) AS filled, CAST(is_crm_sent AS UNSIGNED) AS sent')->get();
    if ($rows->count() !== 1) {
        throw new RuntimeException('Expected one uniquely marked website fixture');
    }
    $lead = $rows->first();
    if ((int)$lead->classified_category !== 3895 || (int)$lead->filled !== 1 || (int)$lead->total_credit !== 400000) {
        throw new RuntimeException('Website fixture is outside the reviewed positive case');
    }
    $matches = static function (string $raw) use ($lead): bool {
        $payload = json_decode($raw, true, flags: JSON_THROW_ON_ERROR);
        return ($payload['displayName'] ?? '') === 'App\\Jobs\\ProcessSendLeadBfl'
            && ($payload['data']['commandName'] ?? '') === 'App\\Jobs\\ProcessSendLeadBfl'
            && preg_match('/leadId";i:(\d+);/', $payload['data']['command'] ?? '', $match)
            && (int)$match[1] === (int)$lead->id;
    };
    $candidates = [];
    foreach ($db::table('jobs')->get() as $job) {
        if (!array_key_exists((string)$job->id, $baseline['jobs'])) {
            if (!$matches($job->payload)) {
                throw new RuntimeException('Unexpected new website job');
            }
            $candidates[] = $job;
        }
    }
    $queue = 'local_native_site_20261106_v1';
    if ($phase === 'retry') {
        // Retain the failed attempt; enqueue a new instance of the same real site job.
        $original = json_decode(getenv('MBK_NATIVE_SITE_ORIGINAL_BASELINE_JSON') ?: '', true, flags: JSON_THROW_ON_ERROR);
        if (($original['status'] ?? '') !== 'passed' || ($original['phase'] ?? '') !== 'baseline'
            || $lead->crm_lead_id !== null || (int)$lead->sent !== 1 || $candidates !== []) {
            throw new RuntimeException('Retry requires the retained failed website attempt');
        }
        foreach (['jobs', 'failed_jobs'] as $table) {
            $current = $snapshot($table);
            foreach ($original[$table] as $id => $sha) {
                if (($current[$id] ?? '') !== $sha) {
                    throw new RuntimeException('An original queue row changed before retry');
                }
            }
        }
        $failed = $db::table('failed_jobs')->whereNotIn('id', array_keys($original['failed_jobs']) ?: [0])->get();
        $expectedFailures = filter_var(getenv('MBK_NATIVE_SITE_EXPECT_RETAINED_FAILED') ?: '1', FILTER_VALIDATE_INT);
        if (!in_array($expectedFailures, [1, 2], true) || $failed->count() !== $expectedFailures
            || $snapshot('failed_jobs') !== $baseline['failed_jobs']) {
            throw new RuntimeException('The exact bounded set of failed attempts must be preserved');
        }
        $retainedIds = [];
        foreach ($failed as $attempt) {
            if (!$matches($attempt->payload)) {
                throw new RuntimeException('An unrelated failed job appeared before retry');
            }
            $retainedIds[] = (int)$attempt->id;
        }
        $newId = $db::transaction(static function () use ($db, $lead, $queue): int {
            $current = $db::table('leads_bfl')->where('id', $lead->id)->lockForUpdate()->first();
            if ($current === null || $current->crm_lead_id !== null
                || $db::table('jobs')->where('queue', $queue)->exists()) {
                throw new RuntimeException('Retry was already dispatched or its website identity changed');
            }
            $job = new App\Jobs\ProcessSendLeadBfl((int)$lead->id);
            $job->onQueue($queue);
            return (int)app(Illuminate\Contracts\Bus\Dispatcher::class)->dispatch($job);
        });
        $job = $db::table('jobs')->where('id', $newId)->first();
        if ($job === null || $job->queue !== $queue || (int)$job->attempts !== 0
            || $job->reserved_at !== null || !$matches($job->payload)) {
            throw new RuntimeException('Retry job identity did not match the original website input');
        }
        echo json_encode(['status' => 'passed', 'phase' => 'retry_dispatched', 'run_id' => $run,
            'site_lead_id' => (int)$lead->id, 'job_id' => $newId, 'queue' => $queue,
            'retained_failed_job_ids' => $retainedIds,
            'old_queue_rows_preserved' => true], JSON_THROW_ON_ERROR) . PHP_EOL;
        exit(0);
    }
    if ($phase === 'isolate') {
        if (count($candidates) !== 1 || $lead->crm_lead_id !== null || (int)$lead->sent !== 0) {
            throw new RuntimeException('Exactly one fresh, unsent website job is required');
        }
        $id = (int)$candidates[0]->id;
        if ($db::table('jobs')->where('queue', $queue)->exists()) {
            throw new RuntimeException('Native site queue is already occupied');
        }
        $changed = $db::transaction(static function () use ($db, $id, $matches, $queue): int {
            $job = $db::table('jobs')->where('id', $id)->lockForUpdate()->first();
            if ($job === null || $job->queue !== 'default' || $job->reserved_at !== null
                || (int)$job->attempts !== 0 || !$matches($job->payload)) {
                throw new RuntimeException('Fixture job changed before scoped isolation');
            }
            return $db::table('jobs')->where('id', $id)->where('queue', 'default')->whereNull('reserved_at')
                ->where('attempts', 0)->update(['queue' => $queue]);
        });
        if ($changed !== 1) {
            throw new RuntimeException('Exactly one website job must move');
        }
        echo json_encode(['status' => 'passed', 'phase' => $phase, 'run_id' => $run,
            'site_lead_id' => (int)$lead->id, 'job_id' => $id, 'queue' => $queue,
            'old_queue_rows_preserved' => true], JSON_THROW_ON_ERROR) . PHP_EOL;
        exit(0);
    }
    $expectedId = filter_var(getenv('MBK_NATIVE_EXPECTED_BITRIX_ID'), FILTER_VALIDATE_INT);
    if ($expectedId === false || $expectedId <= 0 || $expectedId >= 700000000
        || (int)$lead->crm_lead_id !== $expectedId || (int)$lead->sent !== 1 || $candidates !== []
        || $snapshot('failed_jobs') !== $baseline['failed_jobs']) {
        throw new RuntimeException('Website job did not complete with the verified native Bitrix identity');
    }
    echo json_encode(['status' => 'passed', 'phase' => $phase, 'run_id' => $run,
        'site_lead_id' => (int)$lead->id, 'bitrix_lead_id' => $expectedId,
        'old_queue_rows_preserved' => true, 'new_pending_jobs' => 0, 'new_failed_jobs' => 0,
        'receiver_boundary' => 'native_api_harness'], JSON_THROW_ON_ERROR) . PHP_EOL;
} catch (Throwable $error) {
    echo json_encode(['status' => 'failed', 'error_class' => get_class($error),
        'message' => $error->getMessage()]) . PHP_EOL;
    exit(1);
}
