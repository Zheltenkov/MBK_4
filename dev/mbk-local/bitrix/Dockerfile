# Native Bitrix runtime only; no customer entrypoints, supervisor, cron or secrets.
FROM mbk-local/php:8.4
USER 0:0
RUN docker-php-ext-install -j2 mysqli exif pcntl
WORKDIR /srv
USER 1000:1000
