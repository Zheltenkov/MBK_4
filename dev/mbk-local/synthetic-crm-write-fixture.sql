-- intended_evidence_level=native_apps_pre_receiver
-- data_source=synthetic_fixture
-- scope=pre_receiver_orm_fixture; protocol_simulation applies only to a future
-- separate receiver stub, not to the actual Laravel ORM query performed here.
-- Dedicated local CRM database ONLY. Not a Bitrix installation or dump.
-- Source: cached main/install/mysql/install.sql:154-243,272-280;
-- fresh ORM BUserGroup::getFirstUserId and BGroup::MBK_FORM_MANAGERS_GROUP.
-- No production users, passwords, receiver, workflows or customer records.
-- Import requires an external preflight of INFORMATION_SCHEMA results below.
-- CREATE IF NOT EXISTS does NOT repair or validate an existing table.
-- Existing group110 members must be either absent or exactly this fixture's
-- sole user969001; otherwise stop without changing/cleaning existing rows.

-- Canonical native membership has NO ID column, despite the ORM primaryKey.
CREATE TABLE IF NOT EXISTS b_user_group (
    USER_ID INT NOT NULL,
    GROUP_ID INT NOT NULL,
    DATE_ACTIVE_FROM DATETIME NULL,
    DATE_ACTIVE_TO DATETIME NULL,
    UNIQUE ix_user_group (USER_ID, GROUP_ID),
    INDEX ix_user_group_group (GROUP_ID)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS b_group (
    ID INT NOT NULL AUTO_INCREMENT,
    TIMESTAMP_X TIMESTAMP NULL,
    ACTIVE CHAR(1) NOT NULL DEFAULT 'Y',
    C_SORT INT NOT NULL DEFAULT '100',
    ANONYMOUS CHAR(1) NOT NULL DEFAULT 'N',
    IS_SYSTEM CHAR(1) NOT NULL DEFAULT 'Y',
    NAME VARCHAR(255) NOT NULL,
    DESCRIPTION VARCHAR(255) NULL,
    SECURITY_POLICY TEXT NULL,
    STRING_ID VARCHAR(255) NULL,
    PRIMARY KEY (ID)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- Canonical-column SUBSET, not the full native b_user schema. Legacy intake
-- does not read/join b_user here; this row only keeps fixture identity clear.
-- Disabled/blocked, no password/hash/authentication or administrative rights.
CREATE TABLE IF NOT EXISTS b_user (
    ID INT NOT NULL AUTO_INCREMENT,
    TIMESTAMP_X TIMESTAMP NULL,
    LOGIN VARCHAR(50) NOT NULL,
    PASSWORD VARCHAR(255) NOT NULL,
    ACTIVE CHAR(1) NOT NULL DEFAULT 'Y',
    NAME VARCHAR(50) NULL,
    LAST_NAME VARCHAR(50) NULL,
    EMAIL VARCHAR(255) NULL,
    DATE_REGISTER DATETIME NOT NULL,
    XML_ID VARCHAR(255) NULL,
    EXTERNAL_AUTH_ID VARCHAR(255) NULL,
    BLOCKED CHAR(1) NOT NULL DEFAULT 'N',
    PASSWORD_EXPIRED CHAR(1) NOT NULL DEFAULT 'N',
    PRIMARY KEY (ID),
    UNIQUE ix_login (LOGIN, EXTERNAL_AUTH_ID)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- Preflight evidence. The runner must check types/defaults/nullability/indexes
-- and exact membership before importing the guarded INSERT section below.
SELECT TABLE_NAME, COLUMN_NAME, COLUMN_TYPE, IS_NULLABLE, COLUMN_DEFAULT
FROM INFORMATION_SCHEMA.COLUMNS
WHERE TABLE_SCHEMA = DATABASE()
  AND TABLE_NAME IN ('b_user_group', 'b_group', 'b_user')
ORDER BY TABLE_NAME, ORDINAL_POSITION;

SELECT TABLE_NAME, INDEX_NAME, NON_UNIQUE, SEQ_IN_INDEX, COLUMN_NAME
FROM INFORMATION_SCHEMA.STATISTICS
WHERE TABLE_SCHEMA = DATABASE()
  AND TABLE_NAME IN ('b_user_group', 'b_group', 'b_user')
ORDER BY TABLE_NAME, INDEX_NAME, SEQ_IN_INDEX;

SELECT USER_ID, GROUP_ID, DATE_ACTIVE_FROM, DATE_ACTIVE_TO
FROM b_user_group WHERE GROUP_ID = 110;

-- Guarded local rows: conflicts are never updated, replaced or ignored.
-- Fixed group110 is a source-code namespace, not a copied production group.
INSERT INTO b_group (
    ID, TIMESTAMP_X, ACTIVE, C_SORT, ANONYMOUS, IS_SYSTEM, NAME,
    DESCRIPTION, SECURITY_POLICY, STRING_ID
)
SELECT 110, '2026-11-06 00:00:00', 'N', 100, 'N', 'N',
       'Local protocol simulation group',
       'source=synthetic_orm_fixture; no production permissions',
       NULL, 'local_protocol_fixture_group110'
WHERE NOT EXISTS (SELECT 1 FROM b_group WHERE ID = 110)
  AND NOT EXISTS (SELECT 1 FROM b_user_group WHERE GROUP_ID = 110);

INSERT INTO b_user (
    ID, TIMESTAMP_X, LOGIN, PASSWORD, ACTIVE, NAME, LAST_NAME, EMAIL,
    DATE_REGISTER, XML_ID, EXTERNAL_AUTH_ID, BLOCKED, PASSWORD_EXPIRED
)
SELECT 969001, '2026-11-06 00:00:00', 'local_protocol_fixture_user', '', 'N',
       'Local', 'Fixture', NULL, '2026-11-06 00:00:00',
       'local_protocol_fixture_user969001', NULL, 'Y', 'Y'
WHERE EXISTS (
    SELECT 1 FROM b_group
    WHERE ID = 110 AND STRING_ID = 'local_protocol_fixture_group110'
      AND ACTIVE = 'N' AND IS_SYSTEM = 'N'
)
  AND NOT EXISTS (
    SELECT 1 FROM b_user
    WHERE ID = 969001 OR LOGIN = 'local_protocol_fixture_user'
)
  AND NOT EXISTS (SELECT 1 FROM b_user_group WHERE GROUP_ID = 110);

INSERT INTO b_user_group (USER_ID, GROUP_ID, DATE_ACTIVE_FROM, DATE_ACTIVE_TO)
SELECT 969001, 110, NULL, NULL
WHERE EXISTS (
    SELECT 1 FROM b_group
    WHERE ID = 110 AND STRING_ID = 'local_protocol_fixture_group110'
      AND ACTIVE = 'N' AND IS_SYSTEM = 'N'
)
  AND EXISTS (
    SELECT 1 FROM b_user
    WHERE ID = 969001 AND LOGIN = 'local_protocol_fixture_user'
      AND XML_ID = 'local_protocol_fixture_user969001'
      AND ACTIVE = 'N' AND BLOCKED = 'Y' AND PASSWORD_EXPIRED = 'Y'
      AND PASSWORD = ''
)
  AND NOT EXISTS (SELECT 1 FROM b_user_group WHERE GROUP_ID = 110);

-- Final invariant: MUST return exactly one row with USER_ID969001, markers,
-- disabled/blocked flags. Any other result blocks the HTTP/worker stage.
-- Native getFirstUserId uses first() without ORDER BY/date/user-active checks;
-- multiple memberships therefore cannot be accepted as a stable fixture.
SELECT ug.USER_ID, ug.GROUP_ID, g.STRING_ID, u.XML_ID,
       u.ACTIVE, u.BLOCKED, u.PASSWORD_EXPIRED
FROM b_user_group AS ug
LEFT JOIN b_group AS g ON g.ID = ug.GROUP_ID
LEFT JOIN b_user AS u ON u.ID = ug.USER_ID
WHERE ug.GROUP_ID = 110;
