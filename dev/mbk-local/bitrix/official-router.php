<?php
// The isolated installer is accessible only through explicit authenticated PHP routes.
ini_set('opcache.revalidate_freq', '0');
$path = parse_url($_SERVER['REQUEST_URI'], PHP_URL_PATH);
header('Cache-Control: no-store');
if ($path === '/health') {
    header('Content-Type: application/json');
    echo json_encode(['service' => 'bitrix-official-installation', 'native_kernel_loaded' => false,
        'installation_complete' => false, 'lead_created' => false]);
    return;
}
$user = getenv('MBK_NATIVE_HTTP_USER');
$password = getenv('MBK_NATIVE_HTTP_PASSWORD');
if (!$user || !$password || !hash_equals('Basic ' . base64_encode($user . ':' . $password), $_SERVER['HTTP_AUTHORIZATION'] ?? '')) {
    http_response_code(401);
    return;
}
if ($path === '/__lab/native/kernel' || $path === '/__lab/native/metadata') {
    header('Content-Type: application/json');
    if (!in_array($_SERVER['REQUEST_METHOD'], ['GET', 'POST'], true) || ($path === '/__lab/native/kernel' && $_SERVER['REQUEST_METHOD'] !== 'GET')) {
        http_response_code(405);
        return;
    }
    try {
        require '/lab/native-bootstrap.php';
        native_bootstrap();
        if ($path === '/__lab/native/kernel') {
            $provenance = [];
            if (is_file('/lab/native-api.php')) {
                require '/lab/native-api.php';
                $provenance = native_provenance();
            }
            $reflection = new ReflectionMethod('CCrmLead', 'Add');
            echo json_encode(['scope' => 'bootstrap_diagnostic', 'kernel_loaded' => true,
                'main_version' => SM_VERSION, 'crm_module_loaded' => true, 'lead_created' => false,
                'native_add_declaring_class' => $reflection->getDeclaringClass()->getName(),
                'native_add_source' => $reflection->getFileName(),
                'native_add_source_sha256' => hash_file('sha256', $reflection->getFileName()),
                'factory_enabled' => Bitrix\Crm\Settings\LeadSettings::getCurrent()->isFactoryEnabled(),
                'provenance' => $provenance]);
        } else {
            require '/lab/native-metadata.php';
            echo json_encode(['scope' => 'native_user_fixture_metadata', 'lead_created' => false,
                'fields' => native_metadata($_SERVER['REQUEST_METHOD'] === 'POST')]);
        }
    } catch (Throwable $error) {
        http_response_code(500);
        $metadata = [];
        if (preg_match('/Class "([A-Za-z0-9_\\\\]+)" not found/', $error->getMessage(), $match)) {
            $metadata['missing_class'] = $match[1];
        }
        echo json_encode(['status' => 'failed', 'error_class' => get_class($error),
            'source_file' => basename($error->getFile()), 'source_line' => $error->getLine(),
            'lead_created' => false] + $metadata);
    }
    return;
}
$receiver = getenv('MBK_NATIVE_RECEIVER_PATH');
$operation = $receiver && $path === $receiver ? 'add' : match ($path) {
    '/__lab/rest/crm.lead.get' => 'get',
    '/__lab/rest/crm.lead.update' => 'update',
    default => null,
};
if ($operation !== null) {
    header('Content-Type: application/json');
    if ($_SERVER['REQUEST_METHOD'] !== 'POST') {
        http_response_code(405);
        echo json_encode(['error' => 'method_not_allowed']);
        return;
    }
    if ($operation !== 'get' && getenv('MBK_NATIVE_WRITE_ENABLED') !== '1') {
        http_response_code(503);
        echo json_encode(['error' => 'native_write_not_enabled']);
        return;
    }
    try {
        if (strtolower(trim(explode(';', $_SERVER['CONTENT_TYPE'] ?? '')[0])) !== 'application/json') {
            throw new InvalidArgumentException('Expected JSON content type');
        }
        $raw = file_get_contents('php://input', false, null, 0, 65537);
        if (strlen($raw) > 65536) {
            throw new InvalidArgumentException('Native JSON envelope is too large');
        }
        $input = json_decode($raw, true, 64, JSON_THROW_ON_ERROR);
        if (!is_array($input) || array_is_list($input)) {
            throw new InvalidArgumentException('Expected native JSON object');
        }
        $profile = ['keys_and_types' => array_map('get_debug_type', $input),
            'province_is_moscow' => ($input['ADDRESS_PROVINCE'] ?? null) === 'Москва',
            'registration_is_moscow' => ($input['UF_CRM_1452153725'] ?? null) === 'Москва',
            'fact_is_moscow' => ($input['UF_CRM_1452153749'] ?? null) === 'Москва',
            'monthly_present' => array_key_exists('UF_GENERAL_MONTHLY_PAYMENTS', $input),
            'other_debt_present' => array_key_exists('UF_TOTAL_DEBT_OTHER', $input),
            'status_restored' => ($input['STATUS_ID'] ?? null) === 'RESTORED'];
        file_put_contents('/tmp/native-api-audit.jsonl', json_encode(['event' => 'received', 'operation' => $operation,
            'at_utc' => gmdate('c'), 'profile' => $profile]) . "\n", FILE_APPEND | LOCK_EX);
        require '/lab/native-bootstrap.php';
        native_bootstrap();
        require '/lab/native-metadata.php';
        require '/lab/native-api.php';
        if ($operation === 'add') {
            $result = native_accept($input);
        } elseif ($operation === 'get') {
            $id = $input['id'] ?? null;
            if (is_bool($id) || (!is_int($id) && !(is_string($id) && preg_match('/^[1-9]\d{0,8}$/', $id))) || (int)$id < 1) {
                throw new InvalidArgumentException('Invalid native lead identity');
            }
            $result = native_read((int)$id);
        } else {
            $result = native_update($input);
        }
        echo json_encode(['result' => $result, '_lab' => ['adapter' => 'native_api_harness',
            'factory_enabled' => Bitrix\Crm\Settings\LeadSettings::getCurrent()->isFactoryEnabled(),
            'provenance' => native_provenance()]], JSON_THROW_ON_ERROR);
    } catch (Throwable $error) {
        $status = match (true) {
            $error instanceof JsonException => 400,
            $error instanceof InvalidArgumentException => 422,
            $error instanceof DomainException => 409,
            $error instanceof OutOfBoundsException => 404,
            default => 500,
        };
        http_response_code($status);
        $failure = ['error' => 'native_operation_failed', 'operation' => $operation,
            'error_class' => get_class($error), 'source_file' => basename($error->getFile()),
            'source_line' => $error->getLine(), 'http_status' => $status];
        file_put_contents('/tmp/native-api-audit.jsonl', json_encode(['event' => 'failed', 'at_utc' => gmdate('c')] + $failure) . "\n", FILE_APPEND | LOCK_EX);
        echo json_encode($failure);
    }
    return;
}
// Additional wizard endpoints are admitted only after their actual source is inspected.
$allowed = ['/', '/index.php'];
if (!in_array($path, $allowed, true) || !in_array($_SERVER['REQUEST_METHOD'], ['GET', 'POST'], true)) {
    http_response_code(404);
    return;
}
$file = '/srv/native' . ($path === '/' ? '/index.php' : $path);
if (!is_file($file)) {
    http_response_code(404);
    return;
}
$_SERVER['DOCUMENT_ROOT'] = '/srv/native';
$_SERVER['SERVER_NAME'] = 'bitrix-official';
$_SERVER['HTTP_HOST'] = 'bitrix-official:8000';
$_SERVER['PHP_SELF'] = $path === '/' ? '/index.php' : $path;
$_SERVER['SCRIPT_NAME'] = $_SERVER['PHP_SELF'];
$_SERVER['SCRIPT_FILENAME'] = $file;
require $file;
