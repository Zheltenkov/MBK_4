-- Schemas are empty; no customer dump or campaign is loaded automatically.
CREATE DATABASE IF NOT EXISTS crm CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
CREATE DATABASE IF NOT EXISTS partners CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
GRANT ALL ON crm.* TO 'mbk'@'%';
GRANT ALL ON partners.* TO 'mbk'@'%';

