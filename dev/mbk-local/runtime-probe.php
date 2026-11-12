<?php
// Run in each real PHP container before creating synthetic application records.
$controlId = getenv('MBK_EGRESS_CONTROL_ID');
$target = getenv('MBK_EGRESS_TEST_HOST');
if (!$controlId || !$target) {
    throw new RuntimeException('Run verify_runtime.py with a fresh host positive control');
}
$capture = @file_get_contents('http://capture:8000/health');
if ($capture === false || json_decode($capture, true)['forwarding'] !== false) {
    throw new RuntimeException('Local capture is unavailable');
}
$options = ['http' => [
    'method' => 'POST',
    'header' => 'Content-Type: application/json',
    'content' => '{"case":"php-runtime-isolation-check"}',
    'timeout' => 3,
]];
if (@file_get_contents('http://capture:8000/php-probe', false, stream_context_create($options)) === false) {
    throw new RuntimeException('Local capture POST failed');
}
$targets = [
    [$target, 443],
    ['172.18.0.1', 80], ['172.18.0.1', 443], ['172.18.0.1', 2082],
    ['host.docker.internal', 2082], ['gateway.docker.internal', 2082],
];
$checks = [];
foreach ($targets as [$host, $port]) {
    $connection = @fsockopen($host, $port, $errorCode, $errorMessage, 2);
    $reachable = is_resource($connection);
    if ($reachable) {
        fclose($connection);
        throw new RuntimeException('An external or host service is reachable; stop app tests');
    }
    $checks[] = ['host' => $host, 'port' => $port, 'blocked' => true];
}
echo json_encode(['control_id' => $controlId, 'internal_capture_post' => true, 'external_checks' => $checks]) . PHP_EOL;
