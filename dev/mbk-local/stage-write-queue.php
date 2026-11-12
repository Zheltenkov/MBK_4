<?php
// Guard one new manufactured job; never consume the earlier default queue.
try {
    require '/app/vendor/autoload.php';
    $app = require '/app/bootstrap/app.php';
    $app->instance('request', Illuminate\Http\Request::create('http://site:8000/local-write-verifier'));
    $app->make(Illuminate\Contracts\Http\Kernel::class)->bootstrap();
    $phase = getenv('MBK_WRITE_PHASE') ?: 'observe';
    if (!in_array($phase, ['baseline', 'isolate', 'observe', 'dispatch-repeat', 'dispatch-retry'], true)) {
        throw new RuntimeException('Unknown guarded write phase');
    }
    $db = Illuminate\Support\Facades\DB::class;
    $defaultIds = $db::table('jobs')->where('queue', 'default')->orderBy('id')->pluck('id')->all();
    $failedIds = $db::table('failed_jobs')->orderBy('id')->pluck('id')->all();
    if ($phase === 'baseline') {
        echo json_encode(['status' => 'passed', 'phase' => 'baseline',
            'default_job_ids' => $defaultIds, 'failed_job_ids' => $failedIds,
            'crm_default_job_ids' => $db::table('crm.jobs')->where('queue', 'default')->orderBy('id')->pluck('id')->all()]) . PHP_EOL;
        exit(0);
    }
    $runId = getenv('MBK_LOCAL_WRITE_RUN_ID');
    if (!is_string($runId) || !preg_match('/^local-write-[a-zA-Z0-9]+$/D', $runId)) {
        throw new RuntimeException('Invalid synthetic run identifier');
    }
    $oldDefault = json_decode(getenv('MBK_OLD_DEFAULT_IDS_JSON'), true, flags: JSON_THROW_ON_ERROR);
    $oldFailed = json_decode(getenv('MBK_OLD_FAILED_IDS_JSON'), true, flags: JSON_THROW_ON_ERROR);
    if (!is_array($oldDefault) || !is_array($oldFailed)) {
        throw new RuntimeException('Missing old queue baseline');
    }
    $leads = $db::table('leads_bfl')->where('utm_source', 'local_write_stage')
        ->where('utm_campaign', $runId)
        ->selectRaw('leads_bfl.*, CAST(is_crm_sent AS UNSIGNED) AS local_crm_sent,
            CAST(is_filled_out AS UNSIGNED) AS local_filled')->get();
    if ($leads->count() !== 1) {
        throw new RuntimeException('Expected exactly one newly marked site lead');
    }
    $lead = $leads->first();
    $jobMatches = static function (string $raw) use ($lead): bool {
        $payload = json_decode($raw, true, flags: JSON_THROW_ON_ERROR);
        if (($payload['displayName'] ?? '') !== 'App\\Jobs\\ProcessSendLeadBfl'
            || ($payload['data']['commandName'] ?? '') !== 'App\\Jobs\\ProcessSendLeadBfl') {
            return false;
        }
        // Read the one scalar ID without unserializing any PHP object.
        return preg_match('/leadId";i:(\d+);/', $payload['data']['command'] ?? '', $match)
            && (int)$match[1] === (int)$lead->id;
    };
    $candidateIds = [];
    foreach ($db::table('jobs')->get() as $job) {
        if ($jobMatches($job->payload)) {
            $candidateIds[] = (int)$job->id;
        }
    }
    if (in_array($phase, ['dispatch-repeat', 'dispatch-retry'], true)) {
        $isRetry = $phase === 'dispatch-retry';
        if ($defaultIds !== $oldDefault || $failedIds !== $oldFailed || $candidateIds !== []
            || ($isRetry ? $lead->crm_lead_id !== null : $lead->crm_lead_id === null)
            || (int)$lead->local_crm_sent !== 1) {
            throw new RuntimeException('Repeat requires a completed new fixture and unchanged old queues');
        }
        if ($isRetry) {
            $matchingFailed = 0;
            foreach ($db::table('failed_jobs')->get(['payload']) as $failedJob) {
                $matchingFailed += (int)$jobMatches($failedJob->payload);
            }
            if ($matchingFailed !== 1) {
                throw new RuntimeException('Bound retry requires exactly one retained failed job for this new fixture');
            }
        }
        $repeat = new App\Jobs\ProcessSendLeadBfl((int)$lead->id);
        $repeat->onQueue('local_synthetic_write_stage');
        $newJobId = app(Illuminate\Contracts\Bus\Dispatcher::class)->dispatch($repeat);
        $job = $db::table('jobs')->where('id', $newJobId)->first();
        if ($job === null || $job->queue !== 'local_synthetic_write_stage' || $job->reserved_at !== null
            || (int)$job->attempts !== 0 || !$jobMatches($job->payload)) {
            throw new RuntimeException('New repeat job identity did not match the bound fixture');
        }
        echo json_encode(['status' => 'passed', 'phase' => $isRetry ? 'retry_dispatched' : 'repeat_dispatched', 'run_id' => $runId,
            'lead_id' => (int)$lead->id, 'job_id' => (int)$newJobId,
            'queue' => 'local_synthetic_write_stage', 'evidence_level' => 'protocol_simulation']) . PHP_EOL;
        exit(0);
    }
    if ($phase === 'isolate') {
        if (count($candidateIds) !== 1 || in_array($candidateIds[0], $oldDefault, true)) {
            throw new RuntimeException('New synthetic job identity is ambiguous');
        }
        $newJobId = $candidateIds[0];
        $expectedDefault = [...$oldDefault, $newJobId];
        sort($expectedDefault);
        if ($defaultIds !== $expectedDefault || $failedIds !== $oldFailed) {
            throw new RuntimeException('Queues changed after the baseline');
        }
        $changed = $db::transaction(static function () use ($db, $newJobId, $jobMatches): int {
            $job = $db::table('jobs')->where('id', $newJobId)->lockForUpdate()->first();
            if ($job === null || $job->queue !== 'default' || $job->reserved_at !== null
                || (int)$job->attempts !== 0 || !$jobMatches($job->payload)) {
                throw new RuntimeException('New job is reserved, changed, or outside the fixture scope');
            }
            return $db::table('jobs')->where('id', $newJobId)->where('queue', 'default')
                ->whereNull('reserved_at')->where('attempts', 0)
                ->update(['queue' => 'local_synthetic_write_stage']);
        });
        if ($changed !== 1) {
            throw new RuntimeException('Exactly one new job must move to the isolated queue');
        }
        echo json_encode(['status' => 'passed', 'phase' => 'isolated', 'run_id' => $runId,
            'lead_id' => (int)$lead->id, 'job_id' => $newJobId, 'changed_rows' => $changed,
            'queue' => 'local_synthetic_write_stage', 'old_default_job_ids' => $oldDefault,
            'old_failed_job_ids' => $oldFailed]) . PHP_EOL;
        exit(0);
    }
    if ($defaultIds !== $oldDefault || array_diff($oldFailed, $failedIds)) {
        throw new RuntimeException('An earlier pending or failed job was consumed or removed');
    }
    $newFailedIds = [];
    foreach ($db::table('failed_jobs')->whereNotIn('id', $oldFailed ?: [0])->get() as $job) {
        if (!$jobMatches($job->payload)) {
            throw new RuntimeException('An unrelated new failed job appeared');
        }
        $newFailedIds[] = (int)$job->id;
    }
    $pendingIds = $db::table('jobs')->where('queue', 'local_synthetic_write_stage')->pluck('id')->all();
    $expectedOutcome = getenv('MBK_EXPECT_WRITE_OUTCOME') ?: 'failed';
    if (!in_array($expectedOutcome, ['failed', 'completed'], true)) {
        throw new RuntimeException('Unknown expected write outcome');
    }
    if ($expectedOutcome === 'failed' && ($pendingIds !== [] || count($newFailedIds) !== 1
        || $lead->crm_lead_id !== null || (int)$lead->local_crm_sent !== 1)) {
        throw new RuntimeException('Receiver-boundary failure state differs from expectation');
    }
    if ($expectedOutcome === 'completed' && ($pendingIds !== [] || $newFailedIds !== []
        || $lead->crm_lead_id === null || (int)$lead->local_crm_sent !== 1)) {
        throw new RuntimeException('Protocol-simulation completion state differs from expectation');
    }
    $httpStatus = null;
    if (is_string($lead->crm_response) && preg_match('/Статус:\s*(\d{3})/u', $lead->crm_response, $match)) {
        $httpStatus = (int)$match[1];
    }
    echo json_encode(['status' => 'passed', 'run_id' => $runId,
        'evidence_level' => $expectedOutcome === 'completed' ? 'protocol_simulation' : 'native_apps_pre_receiver',
        'boundary_state' => $expectedOutcome === 'completed' ? 'simulated_response_consumed' : 'legacy_attempt_failed_boundary_unconfirmed',
        'lead' => ['id' => (int)$lead->id, 'category' => (int)$lead->classified_category,
            'filled' => (bool)$lead->local_filled, 'crm_sent' => (bool)$lead->local_crm_sent,
            'crm_lead_id' => $lead->crm_lead_id, 'stored_error_http_status' => $httpStatus,
            'stored_error_is_historical' => $lead->crm_lead_id !== null && $httpStatus !== null],
        'old_default_job_ids' => $defaultIds, 'old_failed_job_ids' => $oldFailed,
        'new_failed_job_ids' => $newFailedIds, 'new_pending_job_ids' => $pendingIds]) . PHP_EOL;
} catch (Throwable $error) {
    echo json_encode(['status' => 'failed', 'phase' => 'guard_or_observe',
        'error_class' => get_class($error), 'message' => $error->getMessage()]) . PHP_EOL;
    exit(1);
}
