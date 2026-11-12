#!/bin/sh
set -eu
test -f /srv/native/index.php
mkdir -p /tmp/bitrix-sessions
exec php -S 0.0.0.0:8000 -t /srv/native /lab/official-router.php
