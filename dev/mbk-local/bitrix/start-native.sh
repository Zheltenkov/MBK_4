#!/bin/sh
set -eu
# Source must be prepared/reviewed before mounting the native module volume.
test -f /srv/native/bitrix/modules/main/start.php
test -f /srv/native/bitrix/modules/main/include/prolog_before.php
test -f /srv/native/bitrix/modules/crm/include.php
mkdir -p /tmp/bitrix-sessions
exec php -S 0.0.0.0:8000 -t /srv/native /lab/router.php
