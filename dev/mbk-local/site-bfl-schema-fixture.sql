-- Isolated local SITE schema addition: no customer data or seed rows.
-- Exact leads_bfl structure from the saved site commit
-- 04aaa4aa28c0fe25cede8ed291a1b86c2eaa8741:
-- www/database/migrations/2025_12_06_000000_create_leads_bfl_table.php.
-- No later migration in the saved migration set mentions leads_bfl.
--
-- Import into the disposable site's selected database, only when leads_bfl
-- is absent. CREATE intentionally fails rather than silently accepting an
-- older/incompatible existing table. No DROP, ALTER, data insertion or USE.
--
-- Scope: real HTTP create -> step-save -> complete may insert its synthetic
-- test row. Use the real session and CSRF flow, QUEUE_CONNECTION=database,
-- and keep all workers/schedulers STOPPED. Completion dispatches an actual
-- ProcessSendLeadBfl job; this fixture does not run it, create CRM records,
-- configure partners or prove delivery. Other site infrastructure, including
-- jobs and Laravel's chosen session store, must already be prepared locally.

SET NAMES utf8mb4;

CREATE TABLE leads_bfl (
    id BIGINT(20) UNSIGNED NOT NULL AUTO_INCREMENT,
    created_at TIMESTAMP NULL DEFAULT NULL,
    updated_at TIMESTAMP NULL DEFAULT NULL,
    session_id VARCHAR(300) NULL DEFAULT NULL,
    is_classified BIT(1) NOT NULL DEFAULT 0,
    classified_category INT(10) UNSIGNED NOT NULL DEFAULT 0,
    is_filled_out BIT(1) NOT NULL DEFAULT 0,
    is_crm_sent BIT(1) NOT NULL DEFAULT 0,
    crm_lead_id BIGINT(19) NULL DEFAULT NULL,
    crm_response TEXT NULL DEFAULT NULL,
    phone VARCHAR(20) NOT NULL,
    fio VARCHAR(255) NULL DEFAULT NULL,
    ym VARCHAR(500) NULL DEFAULT NULL,
    ga VARCHAR(500) NULL DEFAULT NULL,
    utm_term VARCHAR(500) NULL DEFAULT NULL,
    utm_medium VARCHAR(500) NULL DEFAULT NULL,
    utm_source VARCHAR(500) NULL DEFAULT NULL,
    utm_content VARCHAR(500) NULL DEFAULT NULL,
    utm_campaign VARCHAR(500) NULL DEFAULT NULL,
    client_ip VARCHAR(50) NULL DEFAULT NULL,
    birthday DATE NULL DEFAULT NULL,
    reg_address VARCHAR(500) NULL DEFAULT NULL,
    fact_address VARCHAR(500) NULL DEFAULT NULL,
    credit_history INT(10) UNSIGNED NULL DEFAULT NULL,
    duration_delay INT(10) NULL DEFAULT NULL,
    total_credit DECIMAL(20,2) NULL DEFAULT NULL,
    total_credit_pay DECIMAL(20,2) NULL DEFAULT NULL,
    PRIMARY KEY (id) USING BTREE,
    INDEX session_id (session_id) USING BTREE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
