<?php
// Run the actual framework queue:work --once command without unrelated app-command discovery.
// Worker/events/failed-job logging are all original Laravel implementations.
require '/app/vendor/autoload.php';
$app = require '/app/bootstrap/app.php';
$app->make(Illuminate\Contracts\Console\Kernel::class)->bootstrap();
use Illuminate\Support\Facades\DB;

$kind = getenv('MBK_NATIVE_WORKER_KIND');
$cases = [
    'crm' => ['schema' => 'bitrix_official_v1', 'queue' => 'local_native_crm_20261106_v1',
        'class' => 'App\Jobs\SendLeadMicroservice'],
    'partner' => ['schema' => 'partners_genuine_v1', 'queue' => 'local_native_buyer_20261106_v1',
        'class' => 'App\Jobs\LeadSend'],
];
if (!isset($cases[$kind])) {
    throw new RuntimeException('Unsupported native single-worker scope');
}
$case = $cases[$kind];
$id = filter_var(getenv('MBK_GENUINE_WEBSITE_BITRIX_ID'), FILTER_VALIDATE_INT);
if (!$id || $id < 1 || $id >= 700000000 || config('database.connections.mysql.database') !== $case['schema']
    || config('queue.default') !== 'database' || config('app.debug') !== true) {
    throw new RuntimeException('Native single-worker environment/identity mismatch');
}
$rows = DB::table('jobs')->get();
if ($rows->count() !== 1 || DB::table('failed_jobs')->count() !== 0) {
    throw new RuntimeException('Single-worker requires one accounted job and no failed jobs');
}
$job = $rows[0];
$payload = json_decode($job->payload, true, flags: JSON_THROW_ON_ERROR);
if ($job->queue !== $case['queue'] || (int)$job->attempts !== 0 || $job->reserved_at !== null
    || ($payload['displayName'] ?? '') !== $case['class'] || ($payload['data']['commandName'] ?? '') !== $case['class']) {
    throw new RuntimeException('Native job is not fresh/scoped/the expected original class');
}
if ($kind === 'crm') {
    $lead = DB::table('b_crm_lead')->where('ID', $id)->first(['ID', 'ORIGIN_ID']);
    if ($lead === null || $lead->ORIGIN_ID !== 'local-write-native20261106v1'
        || !preg_match('/leadId";i:([0-9]+);/', $payload['data']['command'] ?? '', $part)
        || (int)$part[1] !== $id || !str_contains($payload['data']['command'] ?? '', '"microservice_bankruptcy";')) {
        throw new RuntimeException('Native sender is not bound to the genuine website');
    }
} else {
    $lead = DB::table('leads')->where('lead_id', $id)->first();
    $item = $lead ? DB::table('distribution_queue')->where('lead_id', $lead->id)->first() : null;
    if ($item === null || (int)$item->campaign_id !== 950 || (int)$item->partner_id !== 991001
        || (int)$item->status_id !== 1
        || !preg_match('/queueId";i:([0-9]+);/', $payload['data']['command'] ?? '', $part)
        || (int)$part[1] !== (int)$item->id) {
        throw new RuntimeException('Native delivery job is not bound to the genuine website');
    }
}
$tracking = ['processing' => 0, 'processed' => 0, 'failed' => 0];
foreach ([Illuminate\Queue\Events\JobProcessing::class => 'processing',
          Illuminate\Queue\Events\JobProcessed::class => 'processed',
          Illuminate\Queue\Events\JobFailed::class => 'failed'] as $event => $counter) {
    $app['events']->listen($event, static function ($event) use (&$tracking, $counter, $job): void {
        if ((string)$event->job->getJobId() !== (string)$job->id) {
            throw new RuntimeException('An unaccounted job reached the single worker');
        }
        $tracking[$counter]++;
    });
}
$command = new Illuminate\Queue\Console\WorkCommand($app['queue.worker'], $app['cache.store']);
$command->setLaravel($app);
$input = new Symfony\Component\Console\Input\ArrayInput([
    'connection' => 'database', '--queue' => $case['queue'], '--once' => true,
    '--tries' => 1, '--timeout' => 75, '--sleep' => 0,
]);
$input->setInteractive(false);
$output = new Symfony\Component\Console\Output\BufferedOutput();
$exit = $command->run($input, $output);
$captured = $output->fetch();
$result = ['status' => 'passed', 'scope' => $kind, 'genuine_website_id' => $id,
    'job_id' => $job->id, 'queue' => $case['queue'], 'framework_command_exit' => $exit,
    'native_worker_events' => $tracking, 'jobs_remaining' => DB::table('jobs')->count(),
    'failed_jobs' => DB::table('failed_jobs')->count(),
    'command_source_sha256' => hash_file('sha256', (new ReflectionClass($command))->getFileName()),
    'worker_source_sha256' => hash_file('sha256', (new ReflectionClass($app['queue.worker']))->getFileName()),
    'console_output_sha256' => hash('sha256', $captured), 'console_output_bytes' => strlen($captured),
    'full_artisan_command_discovery_skipped' => true, 'business_source_modified' => false];
if ($exit !== 0 || $tracking !== ['processing' => 1, 'processed' => 1, 'failed' => 0]
    || $result['jobs_remaining'] !== 0 || $result['failed_jobs'] !== 0) {
    $result['status'] = 'failed';
    echo json_encode($result, JSON_THROW_ON_ERROR) . PHP_EOL;
    exit(1);
}
echo json_encode($result, JSON_THROW_ON_ERROR) . PHP_EOL;
