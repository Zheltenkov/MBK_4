<?php
// New lab configuration: never load/copy a customer .settings.php or license key.
return [
    'utf_mode' => ['value' => true, 'readonly' => true],
    'cache_flags' => ['value' => ['config_options' => 0, 'site_domain' => 0], 'readonly' => true],
    'connections' => [
        'value' => [
            'default' => [
                'className' => '\\Bitrix\\Main\\DB\\MysqliConnection',
                'host' => getenv('MBK_NATIVE_DB_HOST'),
                'database' => getenv('MBK_NATIVE_DB_NAME'),
                'login' => getenv('MBK_NATIVE_DB_USER'),
                'password' => getenv('MBK_NATIVE_DB_PASSWORD'),
                'options' => 2,
                'charset' => 'utf8mb4',
                'initCommand' => "SET SESSION sql_mode='NO_ENGINE_SUBSTITUTION'",
            ],
        ],
        'readonly' => true,
    ],
    'exception_handling' => [
        'value' => [
            'debug' => false,
            'handled_errors_types' => E_ALL & ~E_NOTICE & ~E_DEPRECATED,
            'exception_errors_types' => E_ERROR | E_PARSE,
            'log' => ['settings' => ['file' => '/tmp/bitrix-exceptions.log', 'log_size' => 1_048_576]],
        ],
        'readonly' => true,
    ],
    'crypto' => ['value' => ['crypto_key' => getenv('MBK_NATIVE_CRYPTO_KEY')], 'readonly' => true],
];
