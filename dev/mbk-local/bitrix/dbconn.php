<?php
// Legacy and D7 layers must use the same newly created local database.
$DBType = 'mysql';
$DBHost = getenv('MBK_NATIVE_DB_HOST');
$DBName = getenv('MBK_NATIVE_DB_NAME');
$DBLogin = getenv('MBK_NATIVE_DB_USER');
$DBPassword = getenv('MBK_NATIVE_DB_PASSWORD');
$DBDebug = false;
$DBDebugToFile = false;
define('BX_USE_MYSQLI', true);
define('BX_UTF', true);
define('MYSQL_TABLE_TYPE', 'INNODB');
date_default_timezone_set('Europe/Moscow');
