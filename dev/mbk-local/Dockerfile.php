# Shared local-only PHP runtime; no customer entrypoint, cron, or supervisor.
FROM php:8.4-cli@sha256:d63e8b80cd4c6cfd01cfa7c910522e299cdc7ca1a2b6d6563db48df3c17e6c55

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
       libfreetype6-dev libjpeg62-turbo-dev libpng-dev libzip-dev libicu-dev \
       libonig-dev libcurl4-openssl-dev unzip git \
    && docker-php-ext-configure gd --with-freetype --with-jpeg \
    && docker-php-ext-install -j2 bcmath gd intl mbstring pdo_mysql zip \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

# Composer is used only during explicit dependency preparation, never at startup.
COPY --from=composer:2@sha256:8de2184c6de224f4689e8f80ed5f5edaf0030b6b8b7839a22a2bcb6722ac87d0 /usr/bin/composer /usr/local/bin/composer
WORKDIR /app
USER 1000:1000

