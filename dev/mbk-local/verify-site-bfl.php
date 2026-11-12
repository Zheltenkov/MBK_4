<?php
// Read only synthetic rows through the real Laravel connection, without console boot.
try {
require '/app/vendor/autoload.php';
$app = require '/app/bootstrap/app.php';
$app->instance('request', Illuminate\Http\Request::create('http://site:8000/local-fixture-verifier'));
$app->make(Illuminate\Contracts\Http\Kernel::class)->bootstrap();
$rows = Illuminate\Support\Facades\DB::table('leads_bfl')
    ->where('utm_source', 'local_synthetic_fixture')
    ->selectRaw('id, total_credit AS debt, total_credit_pay AS monthly, classified_category AS category,
        CAST(is_filled_out AS UNSIGNED) AS filled, CAST(is_crm_sent AS UNSIGNED) AS crm_sent, crm_lead_id')
    ->orderBy('id')->get()->toArray();
$jobs = Illuminate\Support\Facades\DB::table('jobs')->get(['payload']);
$jobClasses = [];
foreach ($jobs as $job) {
    $class = json_decode($job->payload, true)['displayName'] ?? 'unknown';
    $jobClasses[$class] = ($jobClasses[$class] ?? 0) + 1;
}
$failedCount = Illuminate\Support\Facades\DB::table('failed_jobs')->count();
$pendingCount = $jobClasses['App\\Jobs\\ProcessSendLeadBfl'] ?? 0;
$expected = ['190000:0' => 4707, '299999:0' => 4707, '300000:0' => 3895, '190000:110000' => 3895];
$errors = [];
if (count($rows) !== count($expected)) {
    $errors[] = 'Unexpected number of manufactured leads';
}
$crmSentCount = 0;
$crmStatuses = [];
$storedResponses = Illuminate\Support\Facades\DB::table('leads_bfl')
    ->where('utm_source', 'local_synthetic_fixture')->pluck('crm_response', 'id');
foreach ($storedResponses as $leadId => $response) {
    if (is_string($response) && preg_match('/Статус:\s*(\d{3})/u', $response, $match)) {
        $crmStatuses[$leadId] = (int)$match[1];
    }
}
foreach ($rows as $row) {
    $key = (int)$row->debt . ':' . (int)$row->monthly;
    if (($expected[$key] ?? null) !== (int)$row->category || (int)$row->filled !== 1 || $row->crm_lead_id !== null) {
        $errors[] = 'Synthetic lead state differs from expectation';
    }
    $crmSentCount += (int)$row->crm_sent;
}
$target = parse_url(getenv('BITRIX24_URL'));
if (($target['scheme'] ?? '') !== 'http' || ($target['host'] ?? '') !== 'crm' || ($target['port'] ?? 0) !== 8000) {
    $errors[] = 'Site CRM sender is not the expected local service';
}
if ($pendingCount !== (int)getenv('MBK_EXPECT_PENDING') || $failedCount !== (int)getenv('MBK_EXPECT_FAILED')
    || $crmSentCount !== (int)getenv('MBK_EXPECT_CRM_SENT_COUNT')) {
    $errors[] = 'Queue or sender state differs from expected worker phase';
}
echo json_encode(['status' => $errors === [] ? 'passed' : 'failed', 'source' => 'synthetic_fixture',
    'rows' => $rows, 'pending_site_jobs' => $pendingCount, 'failed_site_jobs' => $failedCount,
    'stored_crm_http_statuses' => $crmStatuses,
    'job_classes' => $jobClasses, 'sender_target' => ['scheme' => $target['scheme'], 'host' => $target['host'], 'port' => $target['port']],
    'errors' => $errors], JSON_UNESCAPED_UNICODE) . PHP_EOL;
exit($errors === [] ? 0 : 1);
} catch (Throwable $error) {
    echo json_encode(['status' => 'failed', 'error_class' => get_class($error),
        'stage' => 'local_database_verifier']) . PHP_EOL;
    exit(1);
}
