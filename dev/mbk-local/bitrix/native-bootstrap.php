<?php
// Limited native API lifecycle: original prolog, no customer receiver/epilog/end/terminate.
function native_bootstrap(): void
{
    global $APPLICATION, $USER, $DB, $USER_FIELD_MANAGER;
    $_SERVER['DOCUMENT_ROOT'] = '/srv/native';
    $_SERVER['SERVER_NAME'] = 'bitrix-official';
    $_SERVER['HTTP_HOST'] = 'bitrix-official:8000';
    $_GET = $_POST = $_REQUEST = $_FILES = $_COOKIE = [];
    define('NO_KEEP_STATISTIC', true);
    define('NO_AGENT_STATISTIC', true);
    define('NO_AGENT_CHECK', true);
    require '/srv/native/bitrix/modules/main/include/prolog_before.php';
    if (!Bitrix\Main\Loader::includeModule('crm')) {
        throw new RuntimeException('Native CRM module did not load');
    }
}
