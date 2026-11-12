<?php
// Route local HTTP requests through the unmodified application's front controller.
$requestPath = parse_url($_SERVER['REQUEST_URI'], PHP_URL_PATH);
$publicRoot = realpath('/app/public');
$requestedFile = realpath('/app/public' . $requestPath);
if ($publicRoot !== false && $requestedFile !== false
    && str_starts_with($requestedFile, $publicRoot . DIRECTORY_SEPARATOR)
    && is_file($requestedFile)) {
    return false;
}
require '/app/public/index.php';

