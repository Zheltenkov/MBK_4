<?php
// HTTP client only: credentials stay in this container; no redirect or proxy use.
$request = json_decode(stream_get_contents(STDIN), true, 512, JSON_THROW_ON_ERROR);
$path = $request['path'] ?? '/index.php?lang=ru';
if (!preg_match('~^/index\.php(?:\?[^\r\n]*)?$~', $path)) {
    throw new RuntimeException('Only the local official installer entrypoint is allowed');
}
$fields = $request['fields'] ?? null;
if ($fields !== null) {
    foreach ($fields as $name => &$value) {
        if (is_array($value) && count($value) === 1 && isset($value['env'])) {
            $allowed = ['MBK_NATIVE_DB_HOST', 'MBK_NATIVE_DB_NAME', 'MBK_NATIVE_DB_USER', 'MBK_NATIVE_DB_PASSWORD',
                'MBK_NATIVE_ADMIN_USER', 'MBK_NATIVE_ADMIN_PASSWORD'];
            if (!in_array($value['env'], $allowed, true)) {
                throw new RuntimeException('Unexpected generated environment reference');
            }
            $value = getenv($value['env']);
        }
        if (!is_scalar($value) && $value !== null) {
            throw new RuntimeException('Installer fields must be scalar');
        }
    }
    unset($value);
}
$curl = curl_init('http://127.0.0.1:8000' . $path);
$location = '';
curl_setopt_array($curl, [CURLOPT_RETURNTRANSFER => true, CURLOPT_FOLLOWLOCATION => false,
    CURLOPT_TIMEOUT => 60, CURLOPT_CONNECTTIMEOUT => 3, CURLOPT_PROXY => '',
    CURLOPT_HTTPAUTH => CURLAUTH_BASIC,
    CURLOPT_USERPWD => getenv('MBK_NATIVE_HTTP_USER') . ':' . getenv('MBK_NATIVE_HTTP_PASSWORD'),
    CURLOPT_COOKIEJAR => '/tmp/official-wizard-cookies.txt', CURLOPT_COOKIEFILE => '/tmp/official-wizard-cookies.txt',
    CURLOPT_HEADERFUNCTION => static function ($handle, string $header) use (&$location): int {
        if (str_starts_with(strtolower($header), 'location:')) {
            $location = trim(substr($header, 9));
        }
        return strlen($header);
    }]);
if ($fields !== null) {
    curl_setopt($curl, CURLOPT_POST, true);
    curl_setopt($curl, CURLOPT_POSTFIELDS, http_build_query($fields));
}
$body = curl_exec($curl);
if ($body === false) {
    throw new RuntimeException('Local official installer HTTP transport failed');
}
echo json_encode(['status' => curl_getinfo($curl, CURLINFO_RESPONSE_CODE),
    'body_base64' => base64_encode($body), 'body_sha256' => hash('sha256', $body),
    'redirect_private_base64' => base64_encode($location),
    'bytes' => strlen($body)]);
