<?php
// Only explicit lab routes are HTTP-accessible; source/installers are not served.
$path = parse_url($_SERVER['REQUEST_URI'], PHP_URL_PATH);
header('Content-Type: application/json');
header('Cache-Control: no-store');
if ($path === '/health') {
    echo json_encode(['service' => 'bitrix-genuine-bootstrap', 'native_kernel_loaded' => false,
        'lead_created' => false, 'outgoing_forwarder' => false]);
    return;
}
$user = getenv('MBK_NATIVE_HTTP_USER');
$password = getenv('MBK_NATIVE_HTTP_PASSWORD');
$expected = 'Basic ' . base64_encode($user . ':' . $password);
if (!$user || !$password || !hash_equals($expected, $_SERVER['HTTP_AUTHORIZATION'] ?? '')) {
    http_response_code(401);
    echo json_encode(['error' => 'unauthorized']);
    return;
}
if ($path !== '/__lab/kernel' || $_SERVER['REQUEST_METHOD'] !== 'GET') {
    http_response_code(404);
    echo json_encode(['error' => 'not_found']);
    return;
}
try {
    require '/lab/kernel.php';
} catch (Throwable $error) {
    http_response_code(500);
    // Report a source location, not request payloads, SQL text, or credentials.
    $metadata = [];
    if (preg_match('/Class "([A-Za-z0-9_\\\\]+)" not found/', $error->getMessage(), $match)) {
        $metadata['missing_class'] = $match[1];
    }
    echo json_encode(['status' => 'failed', 'stage' => 'native_kernel_bootstrap',
        'error_class' => get_class($error), 'source_file' => basename($error->getFile()),
        'source_line' => $error->getLine(), 'lead_created' => false] + $metadata);
}
