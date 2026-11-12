-- Synthetic read-model fixture for the isolated local CRM database only.
-- Import into an EMPTY, disposable schema selected by the mysql client.
-- No USE, DROP, DELETE, production records, credentials, receiver or campaigns.
-- CREATE TABLE intentionally fails if a table already exists: do not overlay
-- this reduced projection onto an existing Bitrix database.
--
-- Schema source: saved CRM commit 240fd2ae748e1b383b7de0018824c0c01987f183:
--   app/Services/Classification/{RuleRepository,RuleCode,RuleMatcher}.php
--   app/Models/Classification{RuleSingleProperty,RuleMultipleProperty,
--                           RegionSingleProperty}.php
--   app/Services/{AddressParser,Classification/RegionScopeResolver}.php
--
-- NOT a Bitrix installation or complete CRM schema. It supports read-only
-- RuleRepository/ClassificationEngine, AddressParser and an empty scope-370
-- list. It does not support intake, consent UX, dispatch, Bitrix REST, jobs,
-- delivery, deduplication, actual partner selection or production-rule parity.
--
-- IDs 370/371 are fixed reader namespaces required by the source code.
-- All property/element/enum/location/category IDs below are local fixture IDs.
-- Credit-history values 9255/9257/9258 are input contract codes, not DB copies.

SET NAMES utf8mb4;

CREATE TABLE b_iblock_property (
    ID INT NOT NULL PRIMARY KEY,
    IBLOCK_ID INT NOT NULL,
    CODE VARCHAR(100) NOT NULL,
    INDEX fixture_property_code (IBLOCK_ID, CODE)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE b_iblock_property_enum (
    ID INT NOT NULL PRIMARY KEY,
    PROPERTY_ID INT NOT NULL,
    XML_ID VARCHAR(100) NOT NULL,
    INDEX fixture_enum_property (PROPERTY_ID)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE b_iblock_element (
    ID INT NOT NULL PRIMARY KEY,
    IBLOCK_ID INT NOT NULL,
    ACTIVE CHAR(1) NOT NULL DEFAULT 'Y',
    NAME VARCHAR(255) NOT NULL,
    INDEX fixture_element_active (IBLOCK_ID, ACTIVE)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- Scalar column names must match property IDs, not their CODE strings.
CREATE TABLE b_iblock_element_prop_s371 (
    IBLOCK_ELEMENT_ID INT NOT NULL PRIMARY KEY,
    PROPERTY_91001 TEXT NULL COMMENT 'PRIORITY',
    PROPERTY_91002 TEXT NULL COMMENT 'DEBT_MIN',
    PROPERTY_91003 TEXT NULL COMMENT 'DEBT_MAX',
    PROPERTY_91004 TEXT NULL COMMENT 'RESIDENCE_SCOPE enum ID -> XML_ID',
    PROPERTY_91005 TEXT NULL COMMENT 'FOR_WIDGET enum ID -> XML_ID',
    PROPERTY_91006 TEXT NULL COMMENT 'CATEGORY: synthetic output ID',
    PROPERTY_91007 TEXT NULL COMMENT 'VERTICAL',
    PROPERTY_91008 TEXT NULL COMMENT 'REQ_SITE_CONSENT: NULL off, nonempty on'
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE b_iblock_element_prop_m371 (
    ID INT NOT NULL PRIMARY KEY,
    IBLOCK_ELEMENT_ID INT NOT NULL,
    IBLOCK_PROPERTY_ID INT NOT NULL,
    VALUE TEXT NOT NULL,
    INDEX fixture_multi_element (IBLOCK_ELEMENT_ID),
    INDEX fixture_multi_property (IBLOCK_PROPERTY_ID)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- Empty realty-city list: no invented MBK/Credit.Club geographic coverage.
CREATE TABLE b_iblock_element_prop_s370 (
    IBLOCK_ELEMENT_ID INT NOT NULL PRIMARY KEY,
    PROPERTY_91020 TEXT NULL COMMENT 'LIST_CODE: intentionally no rows'
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE b_sale_loc_type (
    ID INT NOT NULL PRIMARY KEY,
    CODE VARCHAR(40) NOT NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE b_sale_location (
    ID INT NOT NULL PRIMARY KEY,
    PARENT_ID INT NOT NULL,
    TYPE_ID INT NOT NULL,
    INDEX fixture_location_parent (PARENT_ID),
    INDEX fixture_location_type (TYPE_ID)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE b_sale_loc_name (
    LOCATION_ID INT NOT NULL,
    LANGUAGE_ID VARCHAR(5) NOT NULL,
    NAME VARCHAR(255) NOT NULL,
    PRIMARY KEY (LOCATION_ID, LANGUAGE_ID),
    INDEX fixture_location_name (LANGUAGE_ID, NAME)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

START TRANSACTION;

INSERT INTO b_iblock_property (ID, IBLOCK_ID, CODE) VALUES
    (91001, 371, 'PRIORITY'),
    (91002, 371, 'DEBT_MIN'),
    (91003, 371, 'DEBT_MAX'),
    (91004, 371, 'RESIDENCE_SCOPE'),
    (91005, 371, 'FOR_WIDGET'),
    (91006, 371, 'CATEGORY'),
    (91007, 371, 'VERTICAL'),
    (91008, 371, 'REQ_SITE_CONSENT'),
    (91009, 371, 'CREDIT_HISTORY_IN'),
    (91020, 370, 'LIST_CODE');

INSERT INTO b_iblock_property_enum (ID, PROPERTY_ID, XML_ID) VALUES
    (92001, 91004, 'msk'),
    (92002, 91004, 'rf'),
    (92011, 91005, 'no'),
    (92012, 91005, 'yes'),
    (92013, 91005, 'any');

-- source=user_fixture: Andrey's written answer 2026-11-06.
-- These six FAKE rules express only the supplied debt ranges, registration
-- scope and normal/Denis-widget history vocabularies. They are NOT an export
-- of table 371, do not prove real priorities/conditions and do not test route.
-- Ordinary channel: 9255 or 9257; Denis widget: 9257 or 9258.
-- No amount/age/assets/employment constraint is invented.
-- Category IDs are intentionally synthetic and are NOT Bitrix enum values:
-- 990191 = CPA band; 990300 = Moscow/MO BFL; 990301 = other-region BFL.
-- No PARTNER/OFFER/THANKS_URL properties: never use this fixture for intake.
-- REQ_SITE_CONSENT is a rule-output flag, not a fake customer consent.
INSERT INTO b_iblock_element (ID, IBLOCK_ID, ACTIVE, NAME) VALUES
    (94001, 371, 'Y', 'source=user_fixture ordinary CPA band'),
    (94002, 371, 'Y', 'source=user_fixture ordinary Moscow BFL'),
    (94003, 371, 'Y', 'source=user_fixture ordinary regional BFL'),
    (94004, 371, 'Y', 'source=user_fixture Denis CPA band'),
    (94005, 371, 'Y', 'source=user_fixture Denis Moscow BFL'),
    (94006, 371, 'Y', 'source=user_fixture Denis regional BFL');

INSERT INTO b_iblock_element_prop_s371 (
    IBLOCK_ELEMENT_ID, PROPERTY_91001, PROPERTY_91002, PROPERTY_91003,
    PROPERTY_91004, PROPERTY_91005, PROPERTY_91006, PROPERTY_91007,
    PROPERTY_91008
) VALUES
    (94001, '1-1', '190000', '299999', NULL,    '92011', '990191', 'bfl', NULL),
    (94002, '1-2', '300000', NULL,     '92001','92011', '990300', 'bfl', '1'),
    (94003, '1-3', '300000', NULL,     '92002','92011', '990301', 'bfl', '1'),
    (94004, '2-1', '190000', '299999', NULL,    '92012', '990191', 'bfl', NULL),
    (94005, '2-2', '300000', NULL,     '92001','92012', '990300', 'bfl', '1'),
    (94006, '2-3', '300000', NULL,     '92002','92012', '990301', 'bfl', '1');

INSERT INTO b_iblock_element_prop_m371 (
    ID, IBLOCK_ELEMENT_ID, IBLOCK_PROPERTY_ID, VALUE
) VALUES
    (95001, 94001, 91009, '9255'), (95002, 94001, 91009, '9257'),
    (95003, 94002, 91009, '9255'), (95004, 94002, 91009, '9257'),
    (95005, 94003, 91009, '9255'), (95006, 94003, 91009, '9257'),
    (95007, 94004, 91009, '9257'), (95008, 94004, 91009, '9258'),
    (95009, 94005, 91009, '9257'), (95010, 94005, 91009, '9258'),
    (95011, 94006, 91009, '9257'), (95012, 94006, 91009, '9258');

-- source=synthetic_fixture: tiny public-name hierarchy for parser behavior.
-- This is neither complete address data nor customer/lead information.
INSERT INTO b_sale_loc_type (ID, CODE) VALUES
    (93001, 'COUNTRY'), (93002, 'REGION'), (93003, 'CITY'),
    (93004, 'VILLAGE'), (93005, 'SUBREGION');

INSERT INTO b_sale_location (ID, PARENT_ID, TYPE_ID) VALUES
    (93101, 0,     93001),
    (93102, 93101, 93002),
    (93103, 93101, 93002),
    (93104, 93101, 93003),
    (93105, 93102, 93003),
    (93106, 93103, 93003);

INSERT INTO b_sale_loc_name (LOCATION_ID, LANGUAGE_ID, NAME) VALUES
    (93101, 'ru', 'Россия'),
    (93102, 'ru', 'Московская область'),
    (93103, 'ru', 'Тверская область'),
    (93104, 'ru', 'Москва'),
    (93105, 'ru', 'Балашиха'),
    (93106, 'ru', 'Ржев');

COMMIT;

-- Expected READER/ENGINE checks (not claims about production behavior):
-- repository: six rules, ascending priorities 1-1,1-2,1-3,2-1,2-2,2-3.
-- ordinary + history9255/9257: 189999 -> none; 190000/299999 -> 990191;
--   300000 + residenceScope=msk -> 990300; rf -> 990301; null -> none.
-- widget + history9257/9258: same amount/scope results; 9255 -> none.
-- ordinary + history9258 or unknown history -> none.
-- CPA fixture has no scope constraint (any/unknown registration scope).
-- source RuleMatcher ranges are inclusive; Direct has a different exclusive
-- upper bound from settings and is NOT exercised by these rules.
-- residenceScope('Москва') -> msk; residenceScope('Тверская область') -> rf.
-- realtyScope('Ржев') -> rf because scope370 is deliberately empty.
-- AddressParser can resolve its own six-location hierarchy; no campaign-
-- compatibility or full DaData parity follows from that parser check.
