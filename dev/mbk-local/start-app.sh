#!/bin/sh
set -eu
# Storage and bootstrap cache are ephemeral, never copied from a deployed host.
mkdir -p /app/storage/framework/cache/data /app/storage/framework/sessions \
    /app/storage/framework/views /app/storage/logs /app/bootstrap/cache
test -f /app/vendor/autoload.php
test -f /app/public/index.php
exec php -S 0.0.0.0:8000 -t /app/public /local/router.php

