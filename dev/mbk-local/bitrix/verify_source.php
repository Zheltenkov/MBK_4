<?php
// Readonly offline verification: no native kernel, credentials, or application I/O.
$manifest = json_decode(file_get_contents('/manifest.json'), true, 512, JSON_THROW_ON_ERROR);
$verified = 0;
$digests = [];
foreach ($manifest['files'] as $file) {
    $path = $file['path'];
    if (!preg_match('~^(main|crm|currency)/~', $path) || str_contains($path, '..')) {
        throw new RuntimeException('Unexpected module member');
    }
    $actual = hash_file('sha256', '/modules/' . $path);
    if (!hash_equals($file['sha256'], $actual)) {
        throw new RuntimeException('Module source mismatch');
    }
    $digests[] = $path . ':' . $actual;
    $verified++;
}
echo json_encode(['status' => 'passed', 'source_pin' => $manifest['source_pin'],
    'verified_files' => $verified, 'content_manifest_sha256' => hash('sha256', implode("\n", $digests)),
    'native_kernel_loaded' => false]);
