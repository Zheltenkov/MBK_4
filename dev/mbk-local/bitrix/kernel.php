<?php
// Native prolog only. No customer receiver or workflow/robot invocation.
$_SERVER['DOCUMENT_ROOT'] = '/srv/native';
$_SERVER['SERVER_NAME'] = 'bitrix-genuine';
$_SERVER['HTTP_HOST'] = 'bitrix-genuine:8000';
// This is an explicit native API harness, not a general legacy web controller.
// Do not let query/cookie parameters activate login/debug/statistics actions.
$_GET = $_POST = $_REQUEST = $_FILES = $_COOKIE = [];
define('NO_KEEP_STATISTIC', true);
define('NO_AGENT_STATISTIC', true);
define('NO_AGENT_CHECK', true);
require $_SERVER['DOCUMENT_ROOT'] . '/bitrix/modules/main/include/prolog_before.php';
echo json_encode(['status' => 'kernel_loaded', 'diagnostic_only' => true,
    'scope' => 'native_main_kernel_only', 'lead_created' => false,
    'main_version' => defined('SM_VERSION') ? SM_VERSION : null,
    'installed_crm' => Bitrix\Main\ModuleManager::isModuleInstalled('crm')]);
