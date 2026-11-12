<?php
// Account and isolate the one Direct job; never execute CRM workers.
try {
    require '/app/vendor/autoload.php';
    $app = require '/app/bootstrap/app.php';
    $app->instance('request', Illuminate\Http\Request::create('http://site:8000/local-crm-queue-verifier'));
    $app->make(Illuminate\Contracts\Http\Kernel::class)->bootstrap();
    $db = Illuminate\Support\Facades\DB::class;
    $phase = getenv('MBK_CRM_QUEUE_PHASE') ?: 'observe';
    if (!in_array($phase, ['isolate', 'observe'], true)) {
        throw new RuntimeException('Unknown CRM queue guard phase');
    }
    $runId = getenv('MBK_LOCAL_WRITE_RUN_ID');
    if (!is_string($runId) || !preg_match('/^local-write-[a-zA-Z0-9]+$/D', $runId)) {
        throw new RuntimeException('Invalid local write identity');
    }
    $oldDefault = json_decode(getenv('MBK_OLD_CRM_DEFAULT_IDS_JSON'), true, flags: JSON_THROW_ON_ERROR);
    if (!is_array($oldDefault)) {
        throw new RuntimeException('Missing CRM default queue baseline');
    }
    $siteLeads = $db::table('leads_bfl')->where('utm_source', 'local_write_stage')->where('utm_campaign', $runId)->get();
    if ($siteLeads->count() !== 1 || $siteLeads->first()->crm_lead_id === null) {
        throw new RuntimeException('Exactly one completed manufactured site lead is required');
    }
    $receiptId = (int)$siteLeads->first()->crm_lead_id;
    if ($receiptId < 700000000) {
        throw new RuntimeException('Receipt is outside the protocol simulator namespace');
    }
    $matches = static function (string $raw) use ($receiptId): bool {
        $payload = json_decode($raw, true, flags: JSON_THROW_ON_ERROR);
        return ($payload['displayName'] ?? '') === 'App\\Jobs\\SendLeadSodaTechDirect'
            && ($payload['data']['commandName'] ?? '') === 'App\\Jobs\\SendLeadSodaTechDirect'
            && preg_match('/leadId";i:(\d+);/', $payload['data']['command'] ?? '', $match)
            && (int)$match[1] === $receiptId;
    };
    $candidates = [];
    foreach ($db::table('crm.jobs')->get() as $job) {
        if ($matches($job->payload)) {
            $candidates[] = $job;
        }
    }
    if (count($candidates) !== 1) {
        throw new RuntimeException('Expected exactly one unexecuted Direct job for the simulated receipt');
    }
    $job = $candidates[0];
    if ($job->reserved_at !== null || (int)$job->attempts !== 0) {
        throw new RuntimeException('CRM fixture job was reserved or attempted');
    }
    $defaultIds = $db::table('crm.jobs')->where('queue', 'default')->orderBy('id')->pluck('id')->all();
    if ($phase === 'isolate') {
        $expected = [...$oldDefault, (int)$job->id];
        sort($expected);
        if ($job->queue !== 'default' || $defaultIds !== $expected || in_array((int)$job->id, $oldDefault, true)) {
            throw new RuntimeException('Unrelated CRM default queue changes were observed');
        }
        $changed = $db::transaction(static function () use ($db, $job, $matches): int {
            $current = $db::table('crm.jobs')->where('id', $job->id)->lockForUpdate()->first();
            if ($current === null || $current->queue !== 'default' || $current->reserved_at !== null
                || (int)$current->attempts !== 0 || !$matches($current->payload)) {
                throw new RuntimeException('CRM job changed before isolation');
            }
            return $db::table('crm.jobs')->where('id', $job->id)->where('queue', 'default')
                ->whereNull('reserved_at')->where('attempts', 0)->update(['queue' => 'local_protocol_direct_unexecuted']);
        });
        if ($changed !== 1) {
            throw new RuntimeException('Exactly one CRM job must be isolated');
        }
    } elseif ($job->queue !== 'local_protocol_direct_unexecuted' || $defaultIds !== $oldDefault) {
        throw new RuntimeException('A repeat added dispatch or changed the old CRM queue');
    }
    echo json_encode(['status' => 'passed', 'evidence_level' => 'protocol_simulation', 'run_id' => $runId,
        'simulated_receipt_id' => (string)$receiptId, 'direct_job_id' => (int)$job->id,
        'direct_job_count_for_receipt' => 1, 'direct_job_class' => 'App\\Jobs\\SendLeadSodaTechDirect',
        'queue' => 'local_protocol_direct_unexecuted', 'attempts' => 0, 'reserved' => false,
        'old_crm_default_job_ids' => $oldDefault, 'crm_worker_executed' => false,
        'partner_delivery_executed' => false]) . PHP_EOL;
} catch (Throwable $error) {
    echo json_encode(['status' => 'failed', 'error_class' => get_class($error),
        'message' => $error->getMessage()]) . PHP_EOL;
    exit(1);
}
