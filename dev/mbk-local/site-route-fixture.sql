-- Empty landing dictionaries otherwise generate invalid empty route regexes.
-- These are manufactured slugs; no customer page content or contact is copied.
INSERT INTO landing_settings (id,is_active,type,url) VALUES
 (990001,1,'loan','local-synthetic-loan'),
 (990002,1,'refin','local-synthetic-refin');
