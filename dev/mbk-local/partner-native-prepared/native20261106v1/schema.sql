-- OFFLINE PREPARED; EMPTY dedicated native-partner schema ONLY.
SET NAMES utf8mb4;

CREATE TABLE `campaign_statuses` (
                `id` TINYINT UNSIGNED NOT NULL AUTO_INCREMENT,
                `created_at` DATETIME NULL DEFAULT CURRENT_TIMESTAMP,
                `updated_at` DATETIME NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
                `name` VARCHAR(100) NOT NULL,
                PRIMARY KEY (`id`) USING BTREE,
                UNIQUE INDEX `uniq_name` (`name`) USING BTREE
            );

CREATE TABLE `failed_jobs` (
  `id` bigint unsigned NOT NULL AUTO_INCREMENT,
  `uuid` varchar(255) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci NOT NULL,
  `connection` text CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci NOT NULL,
  `queue` text CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci NOT NULL,
  `payload` longtext CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci NOT NULL,
  `exception` longtext CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci NOT NULL,
  `failed_at` timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (`id`),
  UNIQUE KEY `failed_jobs_uuid_unique` (`uuid`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE jobs (
 id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY, queue VARCHAR(255) NOT NULL,
 payload LONGTEXT NOT NULL, attempts TINYINT UNSIGNED NOT NULL,
 reserved_at INT UNSIGNED NULL, available_at INT UNSIGNED NOT NULL,
 created_at INT UNSIGNED NOT NULL, INDEX jobs_queue_index (queue)
) ENGINE=InnoDB;

CREATE TABLE `migrations` (
  `id` int unsigned NOT NULL AUTO_INCREMENT,
  `migration` varchar(255) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci NOT NULL,
  `batch` int NOT NULL,
  PRIMARY KEY (`id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE `moonshine_socialites` (
  `id` bigint unsigned NOT NULL AUTO_INCREMENT,
  `moonshine_user_id` bigint unsigned NOT NULL,
  `driver` varchar(255) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci NOT NULL,
  `identity` varchar(255) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci NOT NULL,
  `created_at` timestamp NULL DEFAULT NULL,
  `updated_at` timestamp NULL DEFAULT NULL,
  PRIMARY KEY (`id`),
  UNIQUE KEY `moonshine_socialites_driver_identity_unique` (`driver`,`identity`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE `moonshine_user_roles` (
  `id` bigint unsigned NOT NULL AUTO_INCREMENT,
  `name` varchar(255) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci NOT NULL,
  `created_at` timestamp NULL DEFAULT NULL,
  `updated_at` timestamp NULL DEFAULT NULL,
  PRIMARY KEY (`id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE `notifications` (
  `id` char(36) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci NOT NULL,
  `type` varchar(255) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci NOT NULL,
  `notifiable_type` varchar(255) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci NOT NULL,
  `notifiable_id` bigint unsigned NOT NULL,
  `data` text CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci NOT NULL,
  `read_at` timestamp NULL DEFAULT NULL,
  `created_at` timestamp NULL DEFAULT NULL,
  `updated_at` timestamp NULL DEFAULT NULL,
  PRIMARY KEY (`id`),
  KEY `notifications_notifiable_type_notifiable_id_index` (`notifiable_type`,`notifiable_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE `partner_programs` (
  `id` tinyint unsigned NOT NULL AUTO_INCREMENT,
  `created_at` timestamp NULL DEFAULT NULL,
  `updated_at` timestamp NULL DEFAULT NULL,
  `name` varchar(255) NOT NULL,
  PRIMARY KEY (`id`) USING BTREE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE `partners` (
  `id` int NOT NULL AUTO_INCREMENT,
  `created_at` datetime DEFAULT CURRENT_TIMESTAMP,
  `updated_at` datetime DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  `name` varchar(255) DEFAULT NULL,
  PRIMARY KEY (`id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE `password_reset_tokens` (
  `email` varchar(255) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci NOT NULL,
  `token` varchar(255) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci NOT NULL,
  `created_at` timestamp NULL DEFAULT NULL,
  PRIMARY KEY (`email`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE `personal_access_tokens` (
  `id` bigint unsigned NOT NULL AUTO_INCREMENT,
  `tokenable_type` varchar(255) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci NOT NULL,
  `tokenable_id` bigint unsigned NOT NULL,
  `name` varchar(255) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci NOT NULL,
  `token` varchar(64) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci NOT NULL,
  `abilities` text CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci,
  `last_used_at` timestamp NULL DEFAULT NULL,
  `expires_at` timestamp NULL DEFAULT NULL,
  `created_at` timestamp NULL DEFAULT NULL,
  `updated_at` timestamp NULL DEFAULT NULL,
  PRIMARY KEY (`id`),
  UNIQUE KEY `personal_access_tokens_token_unique` (`token`),
  KEY `personal_access_tokens_tokenable_type_tokenable_id_index` (`tokenable_type`,`tokenable_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE `queue_statuses` (
                `id` TINYINT UNSIGNED NOT NULL AUTO_INCREMENT,
                `created_at` DATETIME NULL DEFAULT CURRENT_TIMESTAMP,
                `updated_at` DATETIME NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
                `name` VARCHAR(100) NOT NULL,
                PRIMARY KEY (`id`) USING BTREE,
                UNIQUE INDEX `uniq_name` (`name`) USING BTREE
            );

CREATE TABLE `queue_tag_sources` (
                `id` TINYINT UNSIGNED NOT NULL AUTO_INCREMENT,
                `created_at` DATETIME NULL DEFAULT CURRENT_TIMESTAMP,
                `updated_at` DATETIME NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
                `name` VARCHAR(100) NOT NULL,
                PRIMARY KEY (`id`) USING BTREE,
                UNIQUE INDEX `uniq_name` (`name`) USING BTREE
            );

CREATE TABLE `tag_types` (
                `id` TINYINT UNSIGNED NOT NULL AUTO_INCREMENT,
                `created_at` DATETIME NULL DEFAULT CURRENT_TIMESTAMP,
                `updated_at` DATETIME NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
                `name` VARCHAR(100) NOT NULL,
                PRIMARY KEY (`id`) USING BTREE,
                UNIQUE INDEX `uniq_name` (`name`) USING BTREE
            );

CREATE TABLE `transaction_types` (
                `id` TINYINT UNSIGNED NOT NULL AUTO_INCREMENT,
                `created_at` DATETIME NULL DEFAULT CURRENT_TIMESTAMP,
                `updated_at` DATETIME NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
                `sign` TINYINT NOT NULL COMMENT 'множитель: +1 приход, -1 расход',
                `name` VARCHAR(100) NOT NULL,
                PRIMARY KEY (`id`) USING BTREE,
                UNIQUE INDEX `uniq_name` (`name`) USING BTREE
            );

CREATE TABLE `users` (
  `id` bigint unsigned NOT NULL AUTO_INCREMENT,
  `name` varchar(255) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci NOT NULL,
  `email` varchar(255) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci NOT NULL,
  `email_verified_at` timestamp NULL DEFAULT NULL,
  `password` varchar(255) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci NOT NULL,
  `remember_token` varchar(100) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci DEFAULT NULL,
  `created_at` timestamp NULL DEFAULT NULL,
  `updated_at` timestamp NULL DEFAULT NULL,
  PRIMARY KEY (`id`),
  UNIQUE KEY `users_email_unique` (`email`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE `working_days` (
                    `id` BIGINT(19) NOT NULL AUTO_INCREMENT,
                    `day` date NOT NULL,
                    `is_work` bit NOT NULL,
                    PRIMARY KEY (`id`) USING BTREE,
                    INDEX `index_date` (`day`) USING BTREE
                ) ENGINE=InnoDB;

CREATE TABLE `campaigns` (
                `id` SMALLINT UNSIGNED NOT NULL AUTO_INCREMENT,
                `created_at` DATETIME NULL DEFAULT CURRENT_TIMESTAMP,
                `updated_at` DATETIME NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
                `partner_id` INT NOT NULL,
                `status_id` TINYINT UNSIGNED NOT NULL DEFAULT '1',
                `name` VARCHAR(255) NOT NULL,
                `priority` TINYINT UNSIGNED NOT NULL DEFAULT '2' COMMENT '1 = высший',
                `price` DECIMAL(8,2) NOT NULL DEFAULT '0.00',
                `daily_limit` SMALLINT UNSIGNED NOT NULL DEFAULT '0',
                PRIMARY KEY (`id`) USING BTREE,
                INDEX `idx_campaigns_partner` (`partner_id`) USING BTREE,
                INDEX `idx_campaigns_status` (`status_id`) USING BTREE,
                CONSTRAINT `FK_campaigns_partners` FOREIGN KEY (`partner_id`) REFERENCES `partners` (`id`) ON UPDATE CASCADE ON DELETE CASCADE,
                CONSTRAINT `FK_campaigns_statuses` FOREIGN KEY (`status_id`) REFERENCES `campaign_statuses` (`id`) ON UPDATE CASCADE ON DELETE RESTRICT
            );

CREATE TABLE `day_counter` (
  `id` int NOT NULL AUTO_INCREMENT,
  `created_at` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP,
  `updated_at` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  `day` date DEFAULT NULL,
  `partner_program_id` tinyint unsigned DEFAULT NULL,
  `partner_id` int DEFAULT NULL,
  `sent_count` int DEFAULT '0',
  PRIMARY KEY (`id`),
  UNIQUE KEY `partner-day` (`partner_program_id`,`partner_id`,`day`),
  KEY `FK_day_counter_partners` (`partner_id`),
  CONSTRAINT `FK_day_counter_partner_programs` FOREIGN KEY (`partner_program_id`) REFERENCES `partner_programs` (`id`) ON DELETE CASCADE ON UPDATE CASCADE,
  CONSTRAINT `FK_day_counter_partners` FOREIGN KEY (`partner_id`) REFERENCES `partners` (`id`) ON DELETE CASCADE ON UPDATE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE `day_quota` (
  `id` int NOT NULL AUTO_INCREMENT,
  `created_at` datetime DEFAULT CURRENT_TIMESTAMP,
  `updated_at` datetime DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  `partner_program_id` tinyint unsigned DEFAULT NULL,
  `partner_id` int DEFAULT NULL,
  `day_start` date DEFAULT NULL,
  `limit` int DEFAULT NULL,
  PRIMARY KEY (`id`),
  KEY `FK_day_quota_partners` (`partner_id`) USING BTREE,
  KEY `FK_day_quota_partner_programs` (`partner_program_id`),
  CONSTRAINT `FK_day_quota_partner_programs` FOREIGN KEY (`partner_program_id`) REFERENCES `partner_programs` (`id`) ON DELETE CASCADE ON UPDATE CASCADE,
  CONSTRAINT `FK_day_quota_partners` FOREIGN KEY (`partner_id`) REFERENCES `partners` (`id`) ON DELETE RESTRICT ON UPDATE RESTRICT
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE `leads` (
  `id` int NOT NULL AUTO_INCREMENT,
  `created_at` datetime DEFAULT CURRENT_TIMESTAMP,
  `updated_at` datetime DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  `partner_program_id` tinyint unsigned DEFAULT NULL,
  `lead_id` int DEFAULT NULL,
  `title` varchar(255) CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci DEFAULT NULL,
  `phone` bigint DEFAULT NULL,
  `data` json DEFAULT NULL,
  PRIMARY KEY (`id`),
  UNIQUE KEY `lead_id` (`lead_id`),
  KEY `FK_leads_partner_programs` (`partner_program_id`),
  CONSTRAINT `FK_leads_partner_programs` FOREIGN KEY (`partner_program_id`) REFERENCES `partner_programs` (`id`) ON DELETE CASCADE ON UPDATE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE `matrix_for_send` (
  `id` int unsigned NOT NULL AUTO_INCREMENT,
  `partner_program_id` tinyint unsigned DEFAULT NULL,
  `partner_id` int DEFAULT NULL,
  `step` tinyint unsigned DEFAULT NULL,
  `is_send` bit(1) DEFAULT b'0',
  PRIMARY KEY (`id`) USING BTREE,
  UNIQUE KEY `partner_id` (`step`,`partner_program_id`,`partner_id`) USING BTREE,
  KEY `FK__partners_matrix` (`partner_id`) USING BTREE,
  KEY `FK_matrix_for_send_partner_programs` (`partner_program_id`),
  CONSTRAINT `FK__partners_matrix` FOREIGN KEY (`partner_id`) REFERENCES `partners` (`id`),
  CONSTRAINT `FK_matrix_for_send_partner_programs` FOREIGN KEY (`partner_program_id`) REFERENCES `partner_programs` (`id`) ON DELETE CASCADE ON UPDATE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE `moonshine_users` (
  `id` bigint unsigned NOT NULL AUTO_INCREMENT,
  `moonshine_user_role_id` bigint unsigned NOT NULL DEFAULT '1',
  `email` varchar(190) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci NOT NULL,
  `password` varchar(255) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci NOT NULL,
  `name` varchar(255) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci NOT NULL,
  `avatar` varchar(255) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci DEFAULT NULL,
  `remember_token` varchar(100) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci DEFAULT NULL,
  `created_at` timestamp NULL DEFAULT NULL,
  `updated_at` timestamp NULL DEFAULT NULL,
  `partner_id` int DEFAULT NULL,
  PRIMARY KEY (`id`),
  UNIQUE KEY `moonshine_users_email_unique` (`email`),
  KEY `moonshine_users_moonshine_user_role_id_foreign` (`moonshine_user_role_id`),
  KEY `FK_moonshine_users_partners` (`partner_id`),
  CONSTRAINT `FK_moonshine_users_partners` FOREIGN KEY (`partner_id`) REFERENCES `partners` (`id`) ON DELETE CASCADE ON UPDATE CASCADE,
  CONSTRAINT `moonshine_users_moonshine_user_role_id_foreign` FOREIGN KEY (`moonshine_user_role_id`) REFERENCES `moonshine_user_roles` (`id`) ON DELETE CASCADE ON UPDATE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE `partner_adjacents` (
                `id` SMALLINT UNSIGNED NOT NULL AUTO_INCREMENT,
                `created_at` DATETIME NULL DEFAULT CURRENT_TIMESTAMP,
                `updated_at` DATETIME NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
                `partner_id` INT NOT NULL,
                `adjacent_id` INT NOT NULL,
                PRIMARY KEY (`id`) USING BTREE,
                UNIQUE INDEX `uniq_pair` (`partner_id`, `adjacent_id`) USING BTREE,
                CONSTRAINT `FK_partner_adjacents_partner` FOREIGN KEY (`partner_id`) REFERENCES `partners` (`id`) ON UPDATE CASCADE ON DELETE CASCADE,
                CONSTRAINT `FK_partner_adjacents_adjacent` FOREIGN KEY (`adjacent_id`) REFERENCES `partners` (`id`) ON UPDATE CASCADE ON DELETE CASCADE
            );

CREATE TABLE `partner_settings` (
  `id` tinyint NOT NULL AUTO_INCREMENT,
  `created_at` timestamp NULL DEFAULT NULL,
  `updated_at` timestamp NULL DEFAULT NULL,
  `partner_id` int NOT NULL,
  `is_active` bit(1) NOT NULL DEFAULT b'0',
  `partner_program_id` tinyint unsigned DEFAULT NULL,
  `callback_url` varchar(2000) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci DEFAULT NULL,
  `email_to_send` varchar(255) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci DEFAULT NULL,
  PRIMARY KEY (`id`) USING BTREE,
  UNIQUE KEY `partner_id` (`partner_program_id`,`partner_id`),
  KEY `FK_partner_settings_partners` (`partner_id`) USING BTREE,
  KEY `FK_partner_settings_partner_programs` (`partner_program_id`) USING BTREE,
  CONSTRAINT `FK_partner_settings_partner_programs` FOREIGN KEY (`partner_program_id`) REFERENCES `partner_programs` (`id`) ON DELETE CASCADE ON UPDATE CASCADE,
  CONSTRAINT `FK_partner_settings_partners` FOREIGN KEY (`partner_id`) REFERENCES `partners` (`id`) ON DELETE CASCADE ON UPDATE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE `tags` (
                `id` SMALLINT UNSIGNED NOT NULL AUTO_INCREMENT,
                `created_at` DATETIME NULL DEFAULT CURRENT_TIMESTAMP,
                `updated_at` DATETIME NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
                `deleted_at` DATETIME NULL DEFAULT NULL,
                `tag_type_id` TINYINT UNSIGNED NOT NULL,
                `value` VARCHAR(255) NOT NULL COMMENT 'неизменяем после создания',
                PRIMARY KEY (`id`) USING BTREE,
                UNIQUE INDEX `uniq_type_value` (`tag_type_id`, `value`) USING BTREE,
                CONSTRAINT `FK_tags_tag_types` FOREIGN KEY (`tag_type_id`) REFERENCES `tag_types` (`id`) ON UPDATE CASCADE ON DELETE RESTRICT
            );

CREATE TABLE `campaign_daily_usage` (
                `id` INT UNSIGNED NOT NULL AUTO_INCREMENT,
                `campaign_id` SMALLINT UNSIGNED NOT NULL,
                `day` DATE NOT NULL,
                `used` SMALLINT UNSIGNED NOT NULL DEFAULT '0',
                PRIMARY KEY (`id`) USING BTREE,
                UNIQUE INDEX `uniq_campaign_day` (`campaign_id`, `day`) USING BTREE,
                CONSTRAINT `FK_daily_usage_campaigns` FOREIGN KEY (`campaign_id`) REFERENCES `campaigns` (`id`) ON UPDATE CASCADE ON DELETE CASCADE
            );

CREATE TABLE `campaign_tags` (
                `campaign_id` SMALLINT UNSIGNED NOT NULL,
                `tag_id` SMALLINT UNSIGNED NOT NULL,
                PRIMARY KEY (`campaign_id`, `tag_id`) USING BTREE,
                INDEX `idx_campaign_tags_tag` (`tag_id`) USING BTREE,
                CONSTRAINT `FK_campaign_tags_campaigns` FOREIGN KEY (`campaign_id`) REFERENCES `campaigns` (`id`) ON UPDATE CASCADE ON DELETE CASCADE,
                CONSTRAINT `FK_campaign_tags_tags` FOREIGN KEY (`tag_id`) REFERENCES `tags` (`id`) ON UPDATE CASCADE ON DELETE CASCADE
            );

CREATE TABLE `delivery_history` (
                `id` INT UNSIGNED NOT NULL AUTO_INCREMENT,
                `phone` VARCHAR(16) NOT NULL,
                `partner_id` INT NOT NULL,
                `scope_tag_id` SMALLINT UNSIGNED NOT NULL,
                `delivered_at` DATETIME NOT NULL,
                PRIMARY KEY (`id`) USING BTREE,
                INDEX `idx_history_frozen` (`scope_tag_id`, `phone`, `delivered_at`) USING BTREE,
                INDEX `idx_history_duplicate` (`partner_id`, `scope_tag_id`, `phone`, `delivered_at`) USING BTREE,
                CONSTRAINT `FK_history_partners` FOREIGN KEY (`partner_id`) REFERENCES `partners` (`id`) ON UPDATE CASCADE ON DELETE CASCADE,
                CONSTRAINT `FK_history_tags` FOREIGN KEY (`scope_tag_id`) REFERENCES `tags` (`id`) ON UPDATE CASCADE ON DELETE CASCADE
            );

CREATE TABLE `distribution_queue` (
                `id` INT UNSIGNED NOT NULL AUTO_INCREMENT,
                `created_at` DATETIME NULL DEFAULT CURRENT_TIMESTAMP,
                `updated_at` DATETIME NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
                `lead_id` INT NOT NULL,
                `campaign_id` SMALLINT UNSIGNED NOT NULL,
                `partner_id` INT NOT NULL,
                `scope_tag_id` SMALLINT UNSIGNED NOT NULL COMMENT 'тег с настройками, по которому шло распределение',
                `status_id` TINYINT UNSIGNED NOT NULL DEFAULT '1',
                `price` DECIMAL(8,2) NOT NULL COMMENT 'снапшот на момент решения',
                `priority` TINYINT UNSIGNED NOT NULL COMMENT 'снапшот на момент решения',
                `delivery_order` TINYINT UNSIGNED NOT NULL COMMENT '1..hands_count',
                `scheduled_at` DATETIME NOT NULL,
                PRIMARY KEY (`id`) USING BTREE,
                INDEX `idx_queue_work` (`status_id`, `scheduled_at`) USING BTREE,
                INDEX `idx_queue_lead` (`lead_id`) USING BTREE,
                INDEX `idx_queue_partner` (`partner_id`) USING BTREE,
                INDEX `idx_queue_campaign` (`campaign_id`) USING BTREE,
                CONSTRAINT `FK_queue_leads` FOREIGN KEY (`lead_id`) REFERENCES `leads` (`id`) ON UPDATE CASCADE ON DELETE CASCADE,
                CONSTRAINT `FK_queue_campaigns` FOREIGN KEY (`campaign_id`) REFERENCES `campaigns` (`id`) ON UPDATE CASCADE ON DELETE CASCADE,
                CONSTRAINT `FK_queue_partners` FOREIGN KEY (`partner_id`) REFERENCES `partners` (`id`) ON UPDATE CASCADE ON DELETE CASCADE,
                CONSTRAINT `FK_queue_tags` FOREIGN KEY (`scope_tag_id`) REFERENCES `tags` (`id`) ON UPDATE CASCADE ON DELETE CASCADE,
                CONSTRAINT `FK_queue_statuses` FOREIGN KEY (`status_id`) REFERENCES `queue_statuses` (`id`) ON UPDATE CASCADE ON DELETE RESTRICT
            );

CREATE TABLE `lead_tags` (
                `lead_id` INT NOT NULL,
                `tag_id` SMALLINT UNSIGNED NOT NULL,
                PRIMARY KEY (`lead_id`, `tag_id`) USING BTREE,
                INDEX `idx_lead_tags_tag` (`tag_id`) USING BTREE,
                CONSTRAINT `FK_lead_tags_leads` FOREIGN KEY (`lead_id`) REFERENCES `leads` (`id`) ON UPDATE CASCADE ON DELETE CASCADE,
                CONSTRAINT `FK_lead_tags_tags` FOREIGN KEY (`tag_id`) REFERENCES `tags` (`id`) ON UPDATE CASCADE ON DELETE CASCADE
            );

CREATE TABLE `regions` (
                `id` TINYINT UNSIGNED NOT NULL AUTO_INCREMENT,
                `created_at` DATETIME NULL DEFAULT CURRENT_TIMESTAMP,
                `updated_at` DATETIME NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
                `tag_id` SMALLINT UNSIGNED NOT NULL,
                `code` CHAR(2) NOT NULL COMMENT 'код субъекта РФ (КЛАДР/ФНС)',
                `aliases` JSON NULL DEFAULT NULL COMMENT 'точные варианты написания',
                `pattern` VARCHAR(255) NULL DEFAULT NULL COMMENT 'PCRE-регулярка для грязных строк, без учёта регистра',
                PRIMARY KEY (`id`) USING BTREE,
                UNIQUE INDEX `uniq_tag` (`tag_id`) USING BTREE,
                UNIQUE INDEX `uniq_code` (`code`) USING BTREE,
                CONSTRAINT `FK_regions_tags` FOREIGN KEY (`tag_id`) REFERENCES `tags` (`id`) ON UPDATE CASCADE ON DELETE CASCADE
            );

CREATE TABLE `send_plan` (
                `id` INT UNSIGNED NOT NULL AUTO_INCREMENT,
                `created_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                `lead_id` INT NOT NULL,
                `partner_id` INT NOT NULL,
                `partner_program_id` TINYINT UNSIGNED NOT NULL,
                `step` INT DEFAULT NULL,
                `scheduled_at` DATETIME NOT NULL,
                PRIMARY KEY (`id`),
                KEY `idx_send_plan_lead` (`lead_id`),
                CONSTRAINT `FK_send_plan_leads` FOREIGN KEY (`lead_id`)
                    REFERENCES `leads` (`id`) ON DELETE CASCADE ON UPDATE CASCADE,
                CONSTRAINT `FK_send_plan_partners` FOREIGN KEY (`partner_id`)
                    REFERENCES `partners` (`id`) ON DELETE CASCADE ON UPDATE CASCADE,
                CONSTRAINT `FK_send_plan_programs` FOREIGN KEY (`partner_program_id`)
                    REFERENCES `partner_programs` (`id`) ON DELETE CASCADE ON UPDATE CASCADE
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE `sent` (
  `id` bigint NOT NULL AUTO_INCREMENT,
  `created_at` datetime DEFAULT CURRENT_TIMESTAMP,
  `updated_at` datetime DEFAULT CURRENT_TIMESTAMP,
  `partner_id` int DEFAULT NULL,
  `partner_program_id` tinyint unsigned DEFAULT NULL,
  `lead_id` int DEFAULT NULL,
  `request` text,
  `response` longtext CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci,
  `parent_id` bigint DEFAULT NULL,
  PRIMARY KEY (`id`),
  KEY `FK_sent_partners` (`partner_id`),
  KEY `FK_sent_leads` (`lead_id`),
  KEY `FK_sent_sent` (`parent_id`),
  KEY `FK_sent_partner_programs` (`partner_program_id`),
  CONSTRAINT `FK_sent_leads` FOREIGN KEY (`lead_id`) REFERENCES `leads` (`id`) ON DELETE RESTRICT ON UPDATE CASCADE,
  CONSTRAINT `FK_sent_partner_programs` FOREIGN KEY (`partner_program_id`) REFERENCES `partner_programs` (`id`) ON DELETE CASCADE ON UPDATE CASCADE,
  CONSTRAINT `FK_sent_partners` FOREIGN KEY (`partner_id`) REFERENCES `partners` (`id`) ON DELETE RESTRICT ON UPDATE CASCADE,
  CONSTRAINT `FK_sent_sent` FOREIGN KEY (`parent_id`) REFERENCES `sent` (`id`) ON DELETE CASCADE ON UPDATE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE `tag_settings` (
                `id` TINYINT UNSIGNED NOT NULL AUTO_INCREMENT,
                `created_at` DATETIME NULL DEFAULT CURRENT_TIMESTAMP,
                `updated_at` DATETIME NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
                `tag_id` SMALLINT UNSIGNED NOT NULL,
                `hands_count` TINYINT UNSIGNED NOT NULL DEFAULT '3' COMMENT 'максимум партнёров на лид',
                `delay_minutes` SMALLINT UNSIGNED NOT NULL DEFAULT '10' COMMENT 'шаг задержки между доставками',
                `frozen_days` SMALLINT UNSIGNED NOT NULL DEFAULT '30' COMMENT 'глобальная заморозка телефона в скоупе',
                `duplicate_days` SMALLINT UNSIGNED NOT NULL DEFAULT '180' COMMENT 'дубль на партнёра в скоупе',
                PRIMARY KEY (`id`) USING BTREE,
                UNIQUE INDEX `uniq_tag` (`tag_id`) USING BTREE,
                CONSTRAINT `FK_tag_settings_tags` FOREIGN KEY (`tag_id`) REFERENCES `tags` (`id`) ON UPDATE CASCADE ON DELETE CASCADE
            );

CREATE TABLE `balance_transactions` (
                `id` INT UNSIGNED NOT NULL AUTO_INCREMENT,
                `created_at` DATETIME NULL DEFAULT CURRENT_TIMESTAMP,
                `partner_id` INT NOT NULL,
                `type_id` TINYINT UNSIGNED NOT NULL,
                `campaign_id` SMALLINT UNSIGNED NULL DEFAULT NULL,
                `queue_id` INT UNSIGNED NULL DEFAULT NULL,
                `amount` DECIMAL(10,2) UNSIGNED NOT NULL COMMENT 'всегда положительная, направление задаёт transaction_types.sign',
                `manager_id` BIGINT UNSIGNED NULL DEFAULT NULL COMMENT 'для ручных операций',
                `comment` VARCHAR(500) NULL DEFAULT NULL,
                PRIMARY KEY (`id`) USING BTREE,
                INDEX `idx_transactions_partner` (`partner_id`, `created_at`) USING BTREE,
                CONSTRAINT `FK_transactions_partners` FOREIGN KEY (`partner_id`) REFERENCES `partners` (`id`) ON UPDATE CASCADE ON DELETE CASCADE,
                CONSTRAINT `FK_transactions_types` FOREIGN KEY (`type_id`) REFERENCES `transaction_types` (`id`) ON UPDATE CASCADE ON DELETE RESTRICT,
                CONSTRAINT `FK_transactions_campaigns` FOREIGN KEY (`campaign_id`) REFERENCES `campaigns` (`id`) ON UPDATE CASCADE ON DELETE SET NULL,
                CONSTRAINT `FK_transactions_queue` FOREIGN KEY (`queue_id`) REFERENCES `distribution_queue` (`id`) ON UPDATE CASCADE ON DELETE SET NULL,
                CONSTRAINT `FK_transactions_managers` FOREIGN KEY (`manager_id`) REFERENCES `moonshine_users` (`id`) ON UPDATE CASCADE ON DELETE SET NULL
            );

CREATE TABLE `distribution_queue_tags` (
                `queue_id` INT UNSIGNED NOT NULL,
                `tag_id` SMALLINT UNSIGNED NOT NULL,
                `source_id` TINYINT UNSIGNED NOT NULL COMMENT 'чей тег: лида или таргетинга кампании',
                PRIMARY KEY (`queue_id`, `tag_id`, `source_id`) USING BTREE,
                INDEX `idx_queue_tags_tag` (`tag_id`) USING BTREE,
                CONSTRAINT `FK_queue_tags_queue` FOREIGN KEY (`queue_id`) REFERENCES `distribution_queue` (`id`) ON UPDATE CASCADE ON DELETE CASCADE,
                CONSTRAINT `FK_queue_tags_tags` FOREIGN KEY (`tag_id`) REFERENCES `tags` (`id`) ON UPDATE CASCADE ON DELETE CASCADE,
                CONSTRAINT `FK_queue_tags_sources` FOREIGN KEY (`source_id`) REFERENCES `queue_tag_sources` (`id`) ON UPDATE CASCADE ON DELETE RESTRICT
            );

ALTER TABLE `partners`
                ADD COLUMN `balance` DECIMAL(10,2) NOT NULL DEFAULT '0.00' AFTER `name`;

ALTER TABLE `sent`
                ADD COLUMN `queue_id` INT UNSIGNED NULL DEFAULT NULL AFTER `partner_program_id`,
                ADD INDEX `idx_sent_queue` (`queue_id`) USING BTREE,
                ADD CONSTRAINT `FK_sent_queue` FOREIGN KEY (`queue_id`) REFERENCES `distribution_queue` (`id`) ON UPDATE CASCADE ON DELETE SET NULL;

ALTER TABLE `partners`
                ADD COLUMN `logo` VARCHAR(255) NULL DEFAULT NULL AFTER `name`;

ALTER TABLE `sent`
                    ADD COLUMN `step` INT(10) NULL DEFAULT NULL AFTER `lead_id`,
                    ADD COLUMN `priority_in_step` INT(10) NULL DEFAULT NULL AFTER `step`;

ALTER TABLE `campaigns`
                ADD COLUMN `label` VARCHAR(500) NULL DEFAULT NULL AFTER `name`;

ALTER TABLE `partners`
                ADD COLUMN `campaign_name` VARCHAR(255) NULL DEFAULT NULL AFTER `name`;

-- source=inferred_from_native_code + user_fixture; NOT native DB parity.
-- No unknown phone_uniq index is invented.
ALTER TABLE leads ADD COLUMN double_count INT NOT NULL DEFAULT 999;
