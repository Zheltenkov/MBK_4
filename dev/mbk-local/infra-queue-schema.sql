-- Only Laravel's local database-queue structure; no customer business data.
CREATE TABLE IF NOT EXISTS crm.jobs (
  id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
  queue VARCHAR(255) NOT NULL, payload LONGTEXT NOT NULL,
  attempts TINYINT UNSIGNED NOT NULL, reserved_at INT UNSIGNED NULL,
  available_at INT UNSIGNED NOT NULL, created_at INT UNSIGNED NOT NULL,
  INDEX jobs_queue_index (queue)
);
CREATE TABLE IF NOT EXISTS site.jobs LIKE crm.jobs;
CREATE TABLE IF NOT EXISTS partners.jobs LIKE crm.jobs;
